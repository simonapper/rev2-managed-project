# -*- coding: utf-8 -*-

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from chats.services.derax.validate import validate_derax_text


class Command(BaseCommand):
    help = "Replay DERAX regression corpus cases through validate_derax_text."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--corpus-file",
            type=str,
            default="chats/tests/fixtures/derax_regression_corpus.json",
            help="Path to JSON corpus file.",
        )
        parser.add_argument(
            "--report-file",
            type=str,
            default="",
            help="Optional JSON report output path.",
        )

    def handle(self, *args, **options) -> None:
        corpus_file = str(options.get("corpus_file") or "").strip()
        report_file = str(options.get("report_file") or "").strip()
        if not corpus_file:
            raise CommandError("Missing --corpus-file.")

        path = Path(corpus_file)
        if not path.exists():
            raise CommandError(f"Corpus file not found: {path}")

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise CommandError(f"Invalid corpus JSON: {exc}")

        if not isinstance(data, list):
            raise CommandError("Corpus root must be a JSON array.")

        rows_out = []
        mismatches = []
        for idx, row in enumerate(data):
            item = dict(row or {})
            case_id = str(item.get("id") or f"case_{idx + 1}").strip()
            expect_ok = bool(item.get("expect_ok"))
            text = str(item.get("text") or "")
            ok, _payload, errors = validate_derax_text(text)
            detail = {
                "id": case_id,
                "expect_ok": expect_ok,
                "actual_ok": bool(ok),
                "errors": list(errors or []),
            }
            rows_out.append(detail)
            if bool(ok) != expect_ok:
                mismatches.append(detail)

        passed = len(rows_out) - len(mismatches)
        total = len(rows_out)
        self.stdout.write(f"DERAX corpus replay: {passed}/{total} matched")
        for row in rows_out:
            status = "OK" if row["expect_ok"] == row["actual_ok"] else "ERR"
            self.stdout.write(f"- {row['id']}: {status}")

        if report_file:
            out_path = Path(report_file)
            if out_path.parent and not out_path.parent.exists():
                out_path.parent.mkdir(parents=True, exist_ok=True)
            report = {
                "corpus_file": str(path),
                "total": total,
                "matched": passed,
                "mismatches": len(mismatches),
                "rows": rows_out,
            }
            out_path.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
            self.stdout.write(f"Saved report: {out_path}")

        if mismatches:
            raise CommandError("One or more corpus cases mismatched expected outcome.")
