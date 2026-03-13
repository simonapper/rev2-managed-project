# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.test import Client
from django.urls import reverse

from chats.services.derax.validate import validate_derax_text
from projects.models import Project, ProjectDocument, WorkItem


@dataclass
class StageResult:
    phase: str
    ok: bool
    detail: str = ""


def _parse_provider_list(raw: str) -> list[str]:
    items = [str(v or "").strip().lower() for v in str(raw or "").split(",")]
    out = [v for v in items if v]
    allowed = {"openai", "anthropic", "deepseek"}
    invalid = [v for v in out if v not in allowed]
    if invalid:
        raise CommandError("Unsupported providers: " + ", ".join(invalid))
    if not out:
        raise CommandError("At least one provider is required.")
    return out


def _parse_model_overrides(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    text = str(raw or "").strip()
    if not text:
        return out
    for item in text.split(","):
        pair = str(item or "").strip()
        if not pair:
            continue
        if ":" not in pair:
            raise CommandError("Invalid --models item: " + pair)
        key, value = pair.split(":", 1)
        provider = str(key or "").strip().lower()
        model = str(value or "").strip()
        if not provider or not model:
            raise CommandError("Invalid --models item: " + pair)
        out[provider] = model
    return out


def _latest_assistant_from_history(rows: list[dict]) -> str:
    for row in reversed(list(rows or [])):
        if str(row.get("role") or "").strip().lower() != "assistant":
            continue
        text = str(row.get("text") or "").strip()
        if text:
            return text
    return ""


class Command(BaseCommand):
    help = "Run a live DERAX stage smoke run (DEFINE->EXECUTE) for one project across providers."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--project-id", type=int, required=True, help="Project id to test.")
        parser.add_argument("--user-id", type=int, default=0, help="User id to run as. Defaults to project owner.")
        parser.add_argument(
            "--providers",
            type=str,
            default="openai,anthropic,deepseek",
            help="Comma-separated providers. Allowed: openai,anthropic,deepseek",
        )
        parser.add_argument(
            "--models",
            type=str,
            default="",
            help="Optional provider:model map. Example: openai:gpt-5.1,anthropic:claude-sonnet-4-5-20250929",
        )
        parser.add_argument(
            "--timeout-seconds",
            type=int,
            default=180,
            help="Soft timeout per provider run.",
        )
        parser.add_argument(
            "--log-file",
            type=str,
            default="",
            help="Optional output log path. Example: logs/derax_stage_smoke.log",
        )
        parser.add_argument(
            "--report-file",
            type=str,
            default="",
            help="Optional JSON report path. Defaults to log file path with .json extension.",
        )
        parser.add_argument(
            "--fail-fast",
            action="store_true",
            help="Stop immediately after the first failing stage.",
        )
        parser.add_argument(
            "--provider-retries",
            type=int,
            default=1,
            help="Additional retry rounds for transient provider failures.",
        )
        parser.add_argument(
            "--retry-wait-seconds",
            type=int,
            default=20,
            help="Wait time between transient provider retry rounds.",
        )

    def handle(self, *args, **options) -> None:
        project_id = int(options["project_id"])
        user_id = int(options.get("user_id") or 0)
        providers = _parse_provider_list(str(options.get("providers") or ""))
        model_overrides = _parse_model_overrides(str(options.get("models") or ""))
        timeout_seconds = int(options.get("timeout_seconds") or 180)
        log_file = str(options.get("log_file") or "").strip()
        report_file = str(options.get("report_file") or "").strip()
        fail_fast = bool(options.get("fail_fast"))
        provider_retries = max(0, int(options.get("provider_retries") or 0))
        retry_wait_seconds = max(1, int(options.get("retry_wait_seconds") or 20))
        log_lines: list[str] = []

        def emit(message: str) -> None:
            text = str(message or "")
            self.stdout.write(text)
            log_lines.append(text)

        project = Project.objects.filter(id=project_id).first()
        if project is None:
            raise CommandError(f"Project {project_id} not found.")

        if user_id > 0:
            user = get_user_model().objects.filter(id=user_id).first()
            if user is None:
                raise CommandError(f"User {user_id} not found.")
        else:
            user = project.owner
        if user is None:
            raise CommandError("No user resolved for smoke run.")

        profile = getattr(user, "profile", None)
        if profile is None:
            raise CommandError("Selected user has no profile row.")

        url = reverse("projects:derax_project_home", args=[project.id])
        client = Client(raise_request_exception=True)
        client.force_login(user)

        emit(f"Live DERAX smoke start. Project={project.id} user={user.id} providers={','.join(providers)}")
        emit("This will write new DERAX history/runs to the selected project.")

        original_provider = str(profile.llm_provider or "").strip()
        original_openai = str(getattr(profile, "openai_model_default", "") or "").strip()
        original_anthropic = str(getattr(profile, "anthropic_model_default", "") or "").strip()
        original_deepseek = str(getattr(profile, "deepseek_model_default", "") or "").strip()

        all_results: dict[str, list[StageResult]] = {}
        transient_retry_attempts: dict[str, int] = {}
        pending_transient_retries: list[str] = []
        try:
            for provider in providers:
                stage_results = self._run_single_provider(
                    client=client,
                    url=url,
                    project=project,
                    provider=provider,
                    profile=profile,
                    model_overrides=model_overrides,
                    timeout_seconds=timeout_seconds,
                    emit=emit,
                    fail_fast=fail_fast,
                )
                all_results[provider] = stage_results
                provider_failed = any(not bool(r.ok) for r in list(stage_results or []))
                provider_transient = self._is_transient_provider_failure(stage_results)
                if provider_failed and provider_transient and provider_retries > 0:
                    transient_retry_attempts[provider] = 0
                    pending_transient_retries.append(provider)
                    emit(f"Transient provider failure detected for {provider}. Deferring retry.")
                    continue
                if fail_fast and provider_failed:
                    emit("Fail-fast triggered. Stopping remaining providers.")
                    break

            retry_round = 0
            while pending_transient_retries and retry_round < provider_retries:
                retry_round += 1
                emit("")
                emit(f"Transient retry round {retry_round}/{provider_retries}")
                current = list(pending_transient_retries)
                pending_transient_retries = []
                for provider in current:
                    transient_retry_attempts[provider] = int(transient_retry_attempts.get(provider, 0)) + 1
                    emit(f"Retrying provider: {provider}")
                    stage_results = self._run_single_provider(
                        client=client,
                        url=url,
                        project=project,
                        provider=provider,
                        profile=profile,
                        model_overrides=model_overrides,
                        timeout_seconds=timeout_seconds,
                        emit=emit,
                        fail_fast=False,
                    )
                    all_results[provider] = stage_results
                    provider_failed = any(not bool(r.ok) for r in list(stage_results or []))
                    provider_transient = self._is_transient_provider_failure(stage_results)
                    if provider_failed and provider_transient and retry_round < provider_retries:
                        pending_transient_retries.append(provider)
                if pending_transient_retries:
                    emit(f"Waiting {retry_wait_seconds}s before next transient retry round...")
                    time.sleep(retry_wait_seconds)
        finally:
            profile.llm_provider = original_provider or "openai"
            profile.openai_model_default = original_openai
            profile.anthropic_model_default = original_anthropic
            profile.deepseek_model_default = original_deepseek
            profile.save(
                update_fields=[
                    "llm_provider",
                    "openai_model_default",
                    "anthropic_model_default",
                    "deepseek_model_default",
                ]
            )

        emit("")
        emit("DERAX smoke summary")
        overall_ok = True
        for provider in providers:
            rows = all_results.get(provider, [])
            ok_count = len([r for r in rows if r.ok])
            total = len(rows)
            provider_ok = ok_count == total and total > 0
            overall_ok = overall_ok and provider_ok
            label = "PASS" if provider_ok else "FAIL"
            emit(f"- {provider}: {label} ({ok_count}/{total})")
            for row in rows:
                marker = "OK" if row.ok else "ERR"
                detail = f" | {row.detail}" if row.detail else ""
                emit(f"  - {row.phase}: {marker}{detail}")

        if log_file:
            try:
                path = Path(log_file)
                if path.parent and not path.parent.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("\n".join(log_lines).rstrip() + "\n", encoding="utf-8")
                self.stdout.write(f"Saved log: {path}")
                if not report_file:
                    report_file = str(path.with_suffix(".json"))
            except Exception as exc:
                raise CommandError(f"Failed to write log file '{log_file}': {exc}")

        if report_file:
            try:
                report_path = Path(report_file)
                if report_path.parent and not report_path.parent.exists():
                    report_path.parent.mkdir(parents=True, exist_ok=True)
                report = {
                    "project_id": int(project.id),
                    "user_id": int(user.id),
                    "providers": providers,
                    "timeout_seconds": int(timeout_seconds),
                    "fail_fast": bool(fail_fast),
                    "overall_ok": bool(overall_ok),
                    "results": {
                        str(provider): [
                            {
                                "phase": str(row.phase),
                                "ok": bool(row.ok),
                                "detail": str(row.detail or ""),
                            }
                            for row in list(all_results.get(provider, []) or [])
                        ]
                        for provider in providers
                    },
                }
                report_path.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
                self.stdout.write(f"Saved report: {report_path}")
            except Exception as exc:
                raise CommandError(f"Failed to write report file '{report_file}': {exc}")

        if not overall_ok:
            raise CommandError("One or more provider stage runs failed.")

    @staticmethod
    def _is_transient_provider_failure(results: list[StageResult]) -> bool:
        failed = [r for r in list(results or []) if not bool(r.ok)]
        if not failed:
            return False
        text = " ".join([str(r.detail or "") for r in failed]).strip().lower()
        if not text:
            return False
        transient_markers = [
            "overloaded",
            "rate limit",
            "rate_limit",
            "error code: 529",
            "http 429",
            "timed out",
            "timeout",
            "connection error",
            "service unavailable",
            "temporarily unavailable",
        ]
        return any(marker in text for marker in transient_markers)

    def _run_single_provider(
        self,
        *,
        client: Client,
        url: str,
        project: Project,
        provider: str,
        profile: Any,
        model_overrides: dict[str, str],
        timeout_seconds: int,
        emit,
        fail_fast: bool,
    ) -> list[StageResult]:
        emit("")
        emit(f"Provider: {provider}")
        profile.llm_provider = provider
        model = str(model_overrides.get(provider) or "").strip()
        if model:
            if provider == "openai":
                profile.openai_model_default = model
            elif provider == "anthropic":
                profile.anthropic_model_default = model
            elif provider == "deepseek":
                profile.deepseek_model_default = model
        profile.save(
            update_fields=[
                "llm_provider",
                "openai_model_default",
                "anthropic_model_default",
                "deepseek_model_default",
            ]
        )

        # Ensure a primary work item exists.
        home_res = client.get(url)
        if home_res.status_code != 200:
            return [StageResult("BOOT", False, f"GET home failed ({home_res.status_code})")]

        # Reset entry point for deterministic flow.
        client.post(url, {"action": "reenter_phase", "target_phase": WorkItem.PHASE_DEFINE})

        started = time.monotonic()
        results: list[StageResult] = []

        def timed_out() -> bool:
            return (time.monotonic() - started) > max(5, int(timeout_seconds))

        def post_ajax(data: dict[str, str]) -> tuple[bool, str]:
            if timed_out():
                return False, "Provider run timed out."
            try:
                response = client.post(url, data, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
            except Exception as exc:
                detail = f"{exc.__class__.__name__}: {exc}"
                trace = traceback.format_exc(limit=8)
                return False, (detail + " | " + trace).strip()
            if response.status_code != 200:
                body = ""
                try:
                    body = response.content.decode("utf-8", errors="ignore")
                except Exception:
                    body = ""
                snippet = " ".join(str(body or "").split())[:1000]
                if snippet:
                    return False, f"HTTP {response.status_code} | {snippet}"
                return False, f"HTTP {response.status_code}"
            try:
                payload = json.loads(response.content.decode("utf-8"))
            except Exception:
                return False, "Non-JSON AJAX response."
            if not bool(payload.get("ok", False)):
                return False, str(payload.get("error") or "Unknown AJAX error.")
            return True, ""

        def post_redirect(data: dict[str, str]) -> tuple[bool, str]:
            if timed_out():
                return False, "Provider run timed out."
            try:
                response = client.post(url, data)
            except Exception as exc:
                detail = f"{exc.__class__.__name__}: {exc}"
                trace = traceback.format_exc(limit=8)
                return False, (detail + " | " + trace).strip()
            if response.status_code not in {302, 303}:
                body = ""
                try:
                    body = response.content.decode("utf-8", errors="ignore")
                except Exception:
                    body = ""
                snippet = " ".join(str(body or "").split())[:1000]
                if snippet:
                    return False, f"Unexpected status {response.status_code} | {snippet}"
                return False, f"Unexpected status {response.status_code}"
            return True, ""

        # DEFINE
        ok, detail = post_ajax(
            {
                "action": "define_llm_turn",
                "phase_user_input": "Smoke test DEFINE. Return a concise destination with success criteria.",
            }
        )
        results.append(StageResult("DEFINE", ok, detail))
        if not ok:
            return results
        ok, detail = self._validate_latest_phase_payload(project=project, phase=WorkItem.PHASE_DEFINE)
        results.append(StageResult("DEFINE_SCHEMA", ok, detail))
        if not ok:
            return results
        ok, detail = post_redirect({"action": "lock_define_and_explore"})
        results.append(StageResult("DEFINE_LOCK", ok, detail))
        if not ok:
            return results

        # EXPLORE
        ok, detail = post_ajax(
            {
                "action": "explore_llm_turn",
                "phase_user_input": "Smoke test EXPLORE. Add adjacent ideas, risks, tradeoffs, and reframes.",
            }
        )
        results.append(StageResult("EXPLORE", ok, detail))
        if not ok:
            return results
        ok, detail = self._validate_latest_phase_payload(project=project, phase=WorkItem.PHASE_EXPLORE)
        results.append(StageResult("EXPLORE_SCHEMA", ok, detail))
        if not ok:
            return results
        ok, detail = post_redirect({"action": "lock_explore_and_refine"})
        results.append(StageResult("EXPLORE_LOCK", ok, detail))
        if not ok:
            return results

        # REFINE
        ok, detail = post_ajax(
            {
                "action": "refine_llm_turn",
                "phase_user_input": "Smoke test REFINE. Synthesize destination, criteria, constraints, non-goals, risks, and tradeoffs.",
            }
        )
        results.append(StageResult("REFINE", ok, detail))
        if not ok:
            return results
        ok, detail = self._validate_latest_phase_payload(project=project, phase=WorkItem.PHASE_REFINE)
        results.append(StageResult("REFINE_SCHEMA", ok, detail))
        if not ok:
            return results
        ok, detail = post_redirect({"action": "lock_refine_stage"})
        results.append(StageResult("REFINE_LOCK", ok, detail))
        if not ok:
            return results

        # APPROVE
        ok, detail = post_ajax(
            {
                "action": "approve_llm_turn",
                "phase_user_input": "Smoke test APPROVE. Validate stability and keep unresolved conflicts as open questions.",
            }
        )
        results.append(StageResult("APPROVE", ok, detail))
        if not ok:
            return results
        ok, detail = self._validate_latest_phase_payload(project=project, phase=WorkItem.PHASE_APPROVE)
        results.append(StageResult("APPROVE_SCHEMA", ok, detail))
        if not ok:
            return results
        ok, detail = post_redirect({"action": "lock_approve_and_execute"})
        results.append(StageResult("APPROVE_LOCK", ok, detail))
        if not ok:
            return results

        # EXECUTE
        ok, detail = post_ajax(
            {
                "action": "execute_llm_turn",
                "phase_user_input": (
                    "Smoke test EXECUTE. Propose 3 concrete artefacts as objects with kind/title/notes. "
                    "Include at least one run_sheet and one checklist."
                ),
            }
        )
        results.append(StageResult("EXECUTE", ok, detail))
        if not ok:
            return results
        ok, detail = self._validate_latest_phase_payload(project=project, phase=WorkItem.PHASE_EXECUTE)
        results.append(StageResult("EXECUTE_SCHEMA", ok, detail))
        return results

    def _validate_latest_phase_payload(self, *, project: Project, phase: str) -> tuple[bool, str]:
        work_item = WorkItem.objects.filter(project=project, is_primary=True).first()
        if work_item is None:
            return False, "Primary work item missing."

        phase_upper = str(phase or "").strip().upper()
        if phase_upper == WorkItem.PHASE_DEFINE:
            raw = _latest_assistant_from_history(list(work_item.derax_define_history or []))
            if not raw:
                return False, "No DEFINE assistant output in history."
            ok, payload, errors = validate_derax_text(raw)
            if not ok:
                return False, "; ".join([str(v) for v in list(errors or []) if str(v).strip()])[:800]
            resolved = str(((payload or {}).get("meta") or {}).get("phase") or "").strip().upper()
            if resolved != WorkItem.PHASE_DEFINE:
                return False, f"meta.phase mismatch: {resolved or '(blank)'}"
            return True, ""

        if phase_upper == WorkItem.PHASE_EXPLORE:
            raw = _latest_assistant_from_history(list(work_item.derax_explore_history or []))
            if not raw:
                return False, "No EXPLORE assistant output in history."
            ok, payload, errors = validate_derax_text(raw)
            if not ok:
                return False, "; ".join([str(v) for v in list(errors or []) if str(v).strip()])[:800]
            resolved = str(((payload or {}).get("meta") or {}).get("phase") or "").strip().upper()
            if resolved != WorkItem.PHASE_EXPLORE:
                return False, f"meta.phase mismatch: {resolved or '(blank)'}"
            return True, ""

        runs = list(work_item.derax_runs or [])
        for row in reversed(runs):
            if str(row.get("phase") or "").strip().upper() != phase_upper:
                continue
            try:
                asset_id = int(row.get("asset_id") or 0)
            except Exception:
                asset_id = 0
            if asset_id <= 0:
                continue
            doc = ProjectDocument.objects.filter(id=asset_id, project=project).first()
            if doc is None:
                continue
            try:
                doc.file.open("rb")
                raw = doc.file.read().decode("utf-8", errors="ignore")
            except Exception:
                raw = ""
            finally:
                try:
                    doc.file.close()
                except Exception:
                    pass
            if not raw.strip():
                continue
            ok, parsed, errors = validate_derax_text(raw)
            if not ok:
                return False, "; ".join([str(v) for v in list(errors or []) if str(v).strip()])[:800]
            resolved = str(((parsed or {}).get("meta") or {}).get("phase") or "").strip().upper()
            if resolved != phase_upper:
                return False, f"meta.phase mismatch: {resolved or '(blank)'}"
            return True, ""
        return False, f"No {phase_upper} payload found in derax_runs."
