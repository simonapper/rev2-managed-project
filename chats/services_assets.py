# -*- coding: utf-8 -*-

from __future__ import annotations

import base64
import binascii
import hashlib
import mimetypes
import os
import re
from typing import Any, Dict, List, Optional

from django.core.files.base import ContentFile

from uploads.models import GeneratedImage


_DATA_URL_RE = re.compile(
    r"data:(image/[a-zA-Z0-9.+-]+);base64,([A-Za-z0-9+/=\s]+)",
    re.IGNORECASE,
)
_FILE_ID_RE = re.compile(r"\b(file-[A-Za-z0-9_-]{6,})\b")
_B64_JSON_RE = re.compile(r'"b64_json"\s*:\s*"([A-Za-z0-9+/=\s]+)"', re.IGNORECASE)


def _ext_for_mime(mime_type: str) -> str:
    ext = mimetypes.guess_extension((mime_type or "").strip().lower()) or ".png"
    if not ext.startswith("."):
        return "." + ext
    return ext


def save_generated_image_bytes(
    *,
    project,
    chat,
    message,
    prompt: str,
    provider: str,
    model: str,
    image_bytes: bytes,
    mime_type: str = "image/png",
    file_id: str = "",
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> GeneratedImage:
    data = bytes(image_bytes or b"")
    if not data:
        raise ValueError("image_bytes is empty")

    sha = hashlib.sha256(data).hexdigest()
    ext = _ext_for_mime(mime_type)
    filename = sha + ext

    obj = GeneratedImage(
        project=project,
        chat=chat,
        message=message,
        provider=(provider or "").strip().lower(),
        model=(model or "").strip(),
        prompt=prompt or "",
        file_id=(file_id or "").strip(),
        mime_type=(mime_type or "image/png").strip().lower(),
        width=width,
        height=height,
        sha256=sha,
    )
    obj.image_file.save(filename, ContentFile(data), save=True)
    return obj


def download_provider_file_to_bytes(provider_file_id: str) -> bytes:
    file_id = (provider_file_id or "").strip()
    if not file_id:
        raise ValueError("Missing provider file id")

    from openai import OpenAI

    client = OpenAI()
    blob = client.files.content(file_id)
    if hasattr(blob, "read"):
        return bytes(blob.read() or b"")
    if isinstance(blob, (bytes, bytearray)):
        return bytes(blob)
    if hasattr(blob, "content") and isinstance(blob.content, (bytes, bytearray)):
        return bytes(blob.content)
    raise ValueError("Unsupported file content response")


def persist_generated_images_from_text(
    *,
    project,
    chat,
    message,
    prompt: str,
    provider: str,
    model: str,
    text: str,
) -> List[GeneratedImage]:
    out: List[GeneratedImage] = []
    raw = str(text or "")
    if not raw.strip():
        return out

    for match in _DATA_URL_RE.finditer(raw):
        mime_type = (match.group(1) or "image/png").strip().lower()
        b64 = re.sub(r"\s+", "", match.group(2) or "")
        if not b64:
            continue

    for match in _B64_JSON_RE.finditer(raw):
        b64 = re.sub(r"\s+", "", match.group(1) or "")
        if not b64:
            continue
        try:
            image_bytes = base64.b64decode(b64, validate=True)
        except (ValueError, binascii.Error):
            continue
        try:
            out.append(
                save_generated_image_bytes(
                    project=project,
                    chat=chat,
                    message=message,
                    prompt=prompt,
                    provider=provider,
                    model=model,
                    image_bytes=image_bytes,
                    mime_type="image/png",
                )
            )
        except Exception:
            continue
        try:
            image_bytes = base64.b64decode(b64, validate=True)
        except (ValueError, binascii.Error):
            continue
        try:
            out.append(
                save_generated_image_bytes(
                    project=project,
                    chat=chat,
                    message=message,
                    prompt=prompt,
                    provider=provider,
                    model=model,
                    image_bytes=image_bytes,
                    mime_type=mime_type,
                )
            )
        except Exception:
            continue

    seen_file_ids = set()
    for match in _FILE_ID_RE.finditer(raw):
        file_id = (match.group(1) or "").strip()
        if not file_id or file_id in seen_file_ids:
            continue
        seen_file_ids.add(file_id)
        try:
            image_bytes = download_provider_file_to_bytes(file_id)
            out.append(
                save_generated_image_bytes(
                    project=project,
                    chat=chat,
                    message=message,
                    prompt=prompt,
                    provider=provider or "openai",
                    model=model,
                    image_bytes=image_bytes,
                    mime_type="image/png",
                    file_id=file_id,
                )
            )
        except Exception:
            continue

    return out
