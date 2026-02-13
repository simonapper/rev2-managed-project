from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass
class _CopilotResult:
    text: str


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        chunks = []
        for item in value:
            if isinstance(item, str):
                if item.strip():
                    chunks.append(item.strip())
                continue
            if isinstance(item, dict):
                txt = str(item.get("text") or item.get("content") or "").strip()
                if txt:
                    chunks.append(txt)
                continue
            txt = str(item).strip()
            if txt:
                chunks.append(txt)
        return "\n".join(chunks).strip()
    return str(value).strip()


def _extract_event_text(event: Any) -> str:
    if event is None:
        return ""
    data = getattr(event, "data", None)
    if data is None:
        return _as_text(event)

    content = getattr(data, "content", None)
    text = _as_text(content)
    if text:
        return text

    for field_name in ("text", "message", "output"):
        text = _as_text(getattr(data, field_name, None))
        if text:
            return text

    if hasattr(data, "to_dict"):
        try:
            payload = data.to_dict()
        except Exception:
            payload = None
        if isinstance(payload, dict):
            text = _as_text(payload.get("content") or payload.get("text") or payload.get("message"))
            if text:
                return text

    return _as_text(data)


def _run_coro(coro):
    try:
        return asyncio.run(coro)
    except RuntimeError as exc:
        if "asyncio.run() cannot be called from a running event loop" not in str(exc):
            raise
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


class _CopilotAdapter:
    def run(self, prompt: str) -> _CopilotResult:
        text = _run_coro(self._run_async(prompt))
        return _CopilotResult(text=text)

    async def _run_async(self, prompt: str) -> str:
        from copilot import CopilotClient

        client = CopilotClient()
        await client.start()
        try:
            session = await client.create_session()
            try:
                event = await session.send_and_wait({"prompt": prompt}, timeout=120.0)
                return _extract_event_text(event)
            finally:
                try:
                    await session.destroy()
                except Exception:
                    pass
        finally:
            try:
                await client.stop()
            except Exception:
                pass


copilot = _CopilotAdapter()

