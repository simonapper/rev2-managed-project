# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
from typing import Any

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Debug Copilot SDK auth/session/round-trip health."

    def add_arguments(self, parser):
        parser.add_argument("--timeout", type=float, default=30.0, help="send_and_wait timeout seconds")
        parser.add_argument(
            "--prompt",
            type=str,
            default="Reply with exactly: ok",
            help="Prompt for Copilot round-trip check",
        )

    def handle(self, *args, **options):
        timeout = float(options["timeout"])
        prompt = str(options["prompt"])
        result = asyncio.run(self._run_debug(prompt=prompt, timeout=timeout))
        if not result:
            raise CommandError("Copilot debug failed. See diagnostics above.")

    async def _run_debug(self, *, prompt: str, timeout: float) -> bool:
        try:
            from copilot import CopilotClient
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"Import error: {type(exc).__name__}: {exc}"))
            return False

        self.stdout.write("Copilot debug start")
        self.stdout.write("Prompt: " + prompt)
        self.stdout.write("Timeout: " + str(timeout) + "s")
        self.stdout.write("Process diagnostics:")
        self.stdout.write("  USERNAME=" + str(os.environ.get("USERNAME", "")))
        self.stdout.write("  USERPROFILE=" + str(os.environ.get("USERPROFILE", "")))
        self.stdout.write("  HOME=" + str(os.environ.get("HOME", "")))
        self.stdout.write("  COPILOT_CONFIG_DIR=" + str(os.environ.get("COPILOT_CONFIG_DIR", "")))
        self.stdout.write("  cwd=" + os.getcwd())

        client = CopilotClient()
        self.stdout.write(self.style.SUCCESS("Client created"))

        try:
            await client.start()
            self.stdout.write(self.style.SUCCESS("Client started"))

            try:
                status = await client.get_status()
                self.stdout.write(self.style.SUCCESS("Status: " + str(status)))
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"Status error: {type(exc).__name__}: {exc}"))

            auth_ok = False
            try:
                auth = await client.get_auth_status()
                auth_ok = bool(getattr(auth, "isAuthenticated", False))
                if auth_ok:
                    self.stdout.write(self.style.SUCCESS("Auth: authenticated"))
                else:
                    self.stdout.write(self.style.WARNING("Auth: NOT authenticated"))
                self.stdout.write("Auth detail: " + str(auth))
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"Auth error: {type(exc).__name__}: {exc}"))

            session = await client.create_session()
            session_id = str(getattr(session, "session_id", ""))
            self.stdout.write(self.style.SUCCESS("Session created: " + session_id))

            events_seen: list[str] = []

            def on_event(ev: Any) -> None:
                et = str(getattr(ev, "type", ""))
                if et:
                    events_seen.append(et)
                    self.stdout.write("Event: " + et)

            unsub = session.on(on_event)
            try:
                try:
                    ev = await session.send_and_wait({"prompt": prompt}, timeout=timeout)
                except Exception as exc:
                    self.stdout.write(self.style.ERROR(f"Round-trip error: {type(exc).__name__}: {exc}"))
                    return False

                self.stdout.write(self.style.SUCCESS("Round-trip completed"))
                if ev is not None:
                    self.stdout.write("Final event: " + str(getattr(ev, "type", "")))
                    data = getattr(ev, "data", None)
                    content = getattr(data, "content", None) if data is not None else None
                    if content:
                        self.stdout.write("Content: " + str(content))
                if not auth_ok:
                    self.stdout.write(
                        self.style.WARNING(
                            "Note: round-trip worked but auth status reported unauthenticated."
                        )
                    )
                return True
            finally:
                try:
                    unsub()
                except Exception:
                    pass
                try:
                    await session.destroy()
                    self.stdout.write(self.style.SUCCESS("Session destroyed"))
                except Exception as exc:
                    self.stdout.write(self.style.WARNING(f"Session destroy warning: {type(exc).__name__}: {exc}"))
        finally:
            try:
                await client.stop()
                self.stdout.write(self.style.SUCCESS("Client stopped"))
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f"Client stop warning: {type(exc).__name__}: {exc}"))
