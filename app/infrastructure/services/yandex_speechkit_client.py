"""Yandex SpeechKit TTS client (API v3 REST) with optional HTTP proxy."""

from __future__ import annotations

import base64
import json
import uuid
from pathlib import Path

import httpx
from loguru import logger

from app.application.pipeline.tts_chunking import chunk_tts_text, concat_audio_to_mp3
from app.application.pipeline.tts_voices import (
    DEFAULT_SPEECHKIT_PITCH_SHIFT,
    DEFAULT_SPEECHKIT_ROLE,
    DEFAULT_SPEECHKIT_SPEED,
    DEFAULT_SPEECHKIT_VOICE,
    SPEECHKIT_MAX_CHARS,
    resolve_role,
)
from app.config import settings

UPLOAD_DIR = Path(__file__).parent.parent.parent.parent / "uploads"
SPEECHKIT_V3_URL = "https://tts.api.cloud.yandex.net/tts/v3/utteranceSynthesis"


class YandexSpeechKitService:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        folder_id: str | None = None,
        proxy: str | None = None,
    ) -> None:
        self._api_key = (api_key if api_key is not None else settings.yandex.speechkit_api_key).strip()
        self._folder_id = (folder_id if folder_id is not None else settings.yandex.folder_id).strip()
        self._proxy = (proxy if proxy is not None else settings.yandex.tts_proxy).strip() or None

    async def synthesize(
        self,
        text: str,
        *,
        voice: str = DEFAULT_SPEECHKIT_VOICE,
        speed: float = DEFAULT_SPEECHKIT_SPEED,
        pitch_shift: float = DEFAULT_SPEECHKIT_PITCH_SHIFT,
        role: str | None = DEFAULT_SPEECHKIT_ROLE,
    ) -> str:
        script = (text or "").strip()
        if not script:
            raise ValueError("Empty text for speech generation")
        if not self._api_key:
            raise ValueError("YANDEX_SPEECHKIT_API_KEY is not configured")

        voice_id = (voice or DEFAULT_SPEECHKIT_VOICE).strip() or DEFAULT_SPEECHKIT_VOICE
        try:
            speed_val = max(0.1, min(3.0, float(speed)))
        except (TypeError, ValueError):
            speed_val = DEFAULT_SPEECHKIT_SPEED
        try:
            pitch_val = max(-1000.0, min(1000.0, float(pitch_shift)))
        except (TypeError, ValueError):
            pitch_val = DEFAULT_SPEECHKIT_PITCH_SHIFT
        role_id = resolve_role(voice_id, role)

        chunks = chunk_tts_text(script, max_chars=SPEECHKIT_MAX_CHARS)
        if not chunks:
            raise ValueError("Empty text for speech generation")

        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        part_paths: list[Path] = []
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(120.0, connect=30.0),
                proxy=self._proxy,
            ) as client:
                for i, chunk in enumerate(chunks):
                    audio = await self._synthesize_chunk_with_retry(
                        client,
                        chunk,
                        voice=voice_id,
                        speed=speed_val,
                        pitch_shift=pitch_val,
                        role=role_id,
                    )
                    part_path = UPLOAD_DIR / f"ysk_part_{uuid.uuid4().hex[:12]}_{i}.mp3"
                    part_path.write_bytes(audio)
                    part_paths.append(part_path)

            out_path = UPLOAD_DIR / f"ysk_{uuid.uuid4().hex[:12]}.mp3"
            concat_audio_to_mp3(part_paths, out_path)
            logger.info(
                f"SpeechKit TTS done voice={voice_id} speed={speed_val} "
                f"pitchShift={pitch_val} role={role_id} chunks={len(chunks)} "
                f"path={out_path}"
            )
            return str(out_path)
        finally:
            for p in part_paths:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass

    async def _synthesize_chunk_with_retry(
        self,
        client: httpx.AsyncClient,
        text: str,
        *,
        voice: str,
        speed: float,
        pitch_shift: float,
        role: str | None,
        attempts: int = 3,
    ) -> bytes:
        import asyncio

        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return await self._synthesize_chunk(
                    client,
                    text,
                    voice=voice,
                    speed=speed,
                    pitch_shift=pitch_shift,
                    role=role,
                )
            except (httpx.ConnectTimeout, httpx.ConnectError, httpx.ProxyError) as exc:
                last_exc = exc
                logger.warning(
                    f"SpeechKit connect failed attempt={attempt}/{attempts}: {exc}"
                )
                if attempt < attempts:
                    await asyncio.sleep(1.5 * attempt)
        assert last_exc is not None
        raise last_exc

    async def _synthesize_chunk(
        self,
        client: httpx.AsyncClient,
        text: str,
        *,
        voice: str,
        speed: float,
        pitch_shift: float,
        role: str | None,
    ) -> bytes:
        hints: list[dict] = [
            {"voice": voice},
            {"speed": str(speed)},
            {"pitchShift": str(pitch_shift)},
        ]
        if role:
            hints.append({"role": role})

        body = {
            "text": text,
            "hints": hints,
            "outputAudioSpec": {
                "containerAudio": {"containerAudioType": "MP3"},
            },
            "unsafeMode": True,
        }
        headers = {
            "Authorization": f"Api-Key {self._api_key}",
            "Content-Type": "application/json",
        }
        if self._folder_id:
            headers["x-folder-id"] = self._folder_id

        response = await client.post(SPEECHKIT_V3_URL, headers=headers, json=body)
        if response.status_code >= 400:
            detail = (response.text or "")[:500]
            raise RuntimeError(
                f"SpeechKit TTS failed HTTP {response.status_code}: {detail}"
            )

        audio = _extract_audio_bytes(response.content)
        if not audio:
            raise RuntimeError("SpeechKit TTS returned empty audio")
        return audio


def _extract_audio_bytes(raw: bytes) -> bytes:
    """Parse v3 REST response (NDJSON / JSON) into concatenated audio bytes."""
    if not raw:
        return b""
    # Rare: raw MP3 body
    if raw[:3] == b"ID3" or raw[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return raw

    text = raw.decode("utf-8", errors="replace").strip()
    chunks: list[bytes] = []

    def _from_obj(obj: object) -> None:
        if not isinstance(obj, dict):
            return
        result = obj.get("result") if "result" in obj else obj
        if not isinstance(result, dict):
            return
        audio_chunk = result.get("audioChunk") or result.get("audio_chunk")
        if isinstance(audio_chunk, dict):
            data = audio_chunk.get("data")
            if isinstance(data, str) and data:
                chunks.append(base64.b64decode(data))
            return
        # Some gateways flatten to data
        data = result.get("data")
        if isinstance(data, str) and data:
            chunks.append(base64.b64decode(data))

    # NDJSON lines
    if "\n" in text:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                _from_obj(json.loads(line))
            except json.JSONDecodeError:
                continue
        if chunks:
            return b"".join(chunks)

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return b""

    if isinstance(parsed, list):
        for item in parsed:
            _from_obj(item)
    else:
        _from_obj(parsed)
    return b"".join(chunks)
