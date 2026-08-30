"""Low-level Sunor API client for Suno music / spoken-narration tasks."""
from __future__ import annotations

import asyncio
import random
import re
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, Callable

import httpx
from loguru import logger

# Sunor top-level model id — platform currently runs Suno V5.5.
SUNOR_MODEL = "suno"
SUNOR_TASK_PATH = "/task"
POLL_INTERVAL_S = 5.0
POST_TIMEOUT_S = 120.0
GET_TIMEOUT_S = 60.0
MAX_RETRIES_TRANSIENT = 5
TRANSIENT_STATUS_CODES = (429, 500, 502, 503, 504)
NO_FALLBACK_CREATE_STATUS = frozenset({400, 401, 402, 403})

_PROGRESS_RE = re.compile(r"(\d+)\s*%?")


@dataclass(frozen=True)
class MusicTrack:
    audio_id: str
    audio_url: str
    variant_index: int = 0
    image_url: str = ""
    lyrics_prompt: str = ""
    title: str = ""


class SunorClientError(RuntimeError):
    """Sunor API error."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        body: Any = None,
        *,
        error_code: str | None = None,
        fallbackable: bool = True,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.body = body
        self.error_code = error_code
        self.fallbackable = fallbackable


def _auth_headers(api_key: str) -> dict[str, str]:
    return {"x-api-key": api_key, "Content-Type": "application/json"}


def build_music_input(
    *,
    prompt: str,
    instrumental: bool,
    custom_mode: bool,
    style: str | None = None,
    title: str | None = None,
    vocal_gender: str | None = None,
    negative_tags: str | None = None,
) -> dict[str, Any]:
    if not custom_mode:
        return {
            "gpt_description_prompt": prompt,
            "make_instrumental": instrumental,
        }

    tags = (style or "").strip()
    if vocal_gender in ("m", "f"):
        vocal_tag = "male vocals" if vocal_gender == "m" else "female vocals"
        tags_lower = tags.lower()
        if vocal_tag not in tags_lower and (
            "male vocal" not in tags_lower and "female vocal" not in tags_lower
        ):
            tags = f"{tags}, {vocal_tag}" if tags else vocal_tag

    inp: dict[str, Any] = {
        "prompt": prompt,
        "make_instrumental": instrumental,
    }
    if tags:
        inp["tags"] = tags
    if title is not None and str(title).strip():
        inp["title"] = str(title).strip()
    if negative_tags is not None and str(negative_tags).strip():
        inp["negative_tags"] = str(negative_tags).strip()
    return inp


def parse_progress(value: Any) -> int:
    if isinstance(value, int):
        return max(0, min(100, value))
    if isinstance(value, float):
        return max(0, min(100, int(value)))
    if isinstance(value, str):
        m = _PROGRESS_RE.search(value.strip())
        if m:
            return max(0, min(100, int(m.group(1))))
    return 0


def parse_music_tracks(output: dict[str, Any] | None) -> list[MusicTrack]:
    if not isinstance(output, dict):
        return []
    result = output.get("result")
    if not isinstance(result, list):
        return []
    tracks: list[MusicTrack] = []
    for i, item in enumerate(result):
        if not isinstance(item, dict):
            continue
        audio_url = item.get("audio_url")
        if not isinstance(audio_url, str) or not audio_url.startswith("http"):
            continue
        clip_id = item.get("id")
        if not isinstance(clip_id, str):
            clip_id = ""
        image_url = item.get("image_url")
        if not isinstance(image_url, str) or not image_url.startswith("http"):
            image_url = ""
        track_title = item.get("title")
        if not isinstance(track_title, str):
            track_title = ""
        lyrics = ""
        meta = item.get("metadata")
        if isinstance(meta, dict):
            for key in ("prompt", "lyrics", "lyrics_prompt"):
                val = meta.get(key)
                if isinstance(val, str) and val.strip():
                    lyrics = val.strip()
                    break
        tracks.append(
            MusicTrack(
                audio_id=clip_id.strip(),
                audio_url=audio_url.strip(),
                variant_index=i,
                image_url=image_url.strip(),
                lyrics_prompt=lyrics,
                title=track_title.strip(),
            )
        )
    return tracks


def _extract_error_code(body: Any) -> str | None:
    if not isinstance(body, dict):
        return None
    code = body.get("error_code")
    if isinstance(code, str) and code.strip():
        return code.strip()
    err = body.get("error")
    if isinstance(err, dict):
        c = err.get("code") or err.get("type")
        if isinstance(c, str) and c.strip():
            return c.strip()
    return None


def build_inspiration_input(
    *,
    gpt_description_prompt: str,
    make_instrumental: bool = False,
) -> dict[str, Any]:
    return {
        "gpt_description_prompt": (gpt_description_prompt or "").strip(),
        "make_instrumental": bool(make_instrumental),
    }


def build_custom_music_input(
    *,
    prompt: str,
    tags: str,
    negative_tags: str | None = None,
    title: str | None = None,
    vocal_gender: str | None = None,
    make_instrumental: bool = False,
) -> dict[str, Any]:
    tags_out = (tags or "").strip()
    if vocal_gender in ("m", "f"):
        vocal_tag = "male vocals" if vocal_gender == "m" else "female vocals"
        tags_lower = tags_out.lower()
        if vocal_tag not in tags_lower and (
            "male vocal" not in tags_lower and "female vocal" not in tags_lower
        ):
            tags_out = f"{tags_out}, {vocal_tag}" if tags_out else vocal_tag
    inp: dict[str, Any] = {
        "prompt": (prompt or "").strip(),
        "make_instrumental": bool(make_instrumental),
    }
    if tags_out:
        inp["tags"] = tags_out
    if negative_tags and str(negative_tags).strip():
        inp["negative_tags"] = str(negative_tags).strip()
    if title and str(title).strip():
        inp["title"] = str(title).strip()
    return inp


def build_instrumental_input(
    *,
    tags: str,
    title: str | None = None,
    negative_tags: str | None = None,
) -> dict[str, Any]:
    inp: dict[str, Any] = {
        "tags": (tags or "").strip(),
        "make_instrumental": True,
    }
    if title and str(title).strip():
        inp["title"] = str(title).strip()
    if negative_tags and str(negative_tags).strip():
        inp["negative_tags"] = str(negative_tags).strip()
    return inp


def build_continue_input(
    *,
    continue_clip_id: str,
    continue_at: int | float,
    prompt: str | None = None,
) -> dict[str, Any]:
    inp: dict[str, Any] = {
        "continue_clip_id": (continue_clip_id or "").strip(),
        "continue_at": max(0, int(continue_at)),
    }
    if prompt and str(prompt).strip():
        inp["prompt"] = str(prompt).strip()
    return inp


def parse_lyrics_text(output: dict[str, Any] | None) -> str:
    if not isinstance(output, dict):
        return ""
    for key in ("text", "lyrics", "result"):
        val = output.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    result = output.get("result")
    if isinstance(result, list) and result:
        first = result[0]
        if isinstance(first, dict):
            for key in ("text", "lyrics", "prompt"):
                val = first.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
    if isinstance(result, dict):
        for key in ("text", "lyrics", "prompt"):
            val = result.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""


def pick_track(tracks: list[MusicTrack], variant: str = "first") -> MusicTrack:
    if not tracks:
        raise SunorClientError("No tracks to pick from", fallbackable=True)
    if variant == "second" and len(tracks) > 1:
        return tracks[1]
    return tracks[0]


async def post_create_task(
    base_url: str,
    api_key: str,
    *,
    task_type: str,
    input_data: dict[str, Any],
    timeout: float = POST_TIMEOUT_S,
) -> str:
    """POST /task for any Sunor task type. Returns task_id."""
    url = base_url.rstrip("/") + SUNOR_TASK_PATH
    body = {
        "model": SUNOR_MODEL,
        "task_type": task_type,
        "input": input_data,
    }
    logger.info(
        "sunor_submit task_type={} model={}",
        task_type,
        SUNOR_MODEL,
    )

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                url,
                json=body,
                headers=_auth_headers(api_key),
                timeout=timeout,
            )
        except httpx.RequestError as e:
            logger.exception("Sunor create request failed: {}", e)
            raise SunorClientError(
                f"Sunor create network error: {e}",
                fallbackable=True,
            ) from e

    try:
        payload = resp.json()
    except Exception:
        payload = resp.text

    request_id = resp.headers.get("X-Request-Id") or resp.headers.get("x-request-id")
    if request_id:
        logger.debug("Sunor create X-Request-Id={}", request_id)

    if resp.status_code not in (200, 202):
        error_code = _extract_error_code(payload)
        no_fallback = resp.status_code in NO_FALLBACK_CREATE_STATUS
        raise SunorClientError(
            f"Sunor create failed: {resp.status_code}",
            status_code=resp.status_code,
            body=payload,
            error_code=error_code,
            fallbackable=not no_fallback,
        )

    if not isinstance(payload, dict):
        raise SunorClientError(
            "Sunor create response not JSON object",
            status_code=resp.status_code,
            body=payload,
            fallbackable=True,
        )
    data = payload.get("data")
    task_id = data.get("task_id") if isinstance(data, dict) else None
    if not isinstance(task_id, str) or not task_id.strip():
        raise SunorClientError(
            "Sunor create response missing task_id",
            status_code=resp.status_code,
            body=payload,
            fallbackable=True,
        )
    return task_id.strip()


async def post_create_music_task(
    base_url: str,
    api_key: str,
    *,
    prompt: str,
    instrumental: bool = False,
    custom_mode: bool = False,
    style: str | None = None,
    title: str | None = None,
    vocal_gender: str | None = None,
    negative_tags: str | None = None,
    timeout: float = POST_TIMEOUT_S,
) -> str:
    """POST /task for Suno (V5.5). Returns task_id."""
    return await post_create_task(
        base_url,
        api_key,
        task_type="music",
        input_data=build_music_input(
            prompt=prompt,
            instrumental=instrumental,
            custom_mode=custom_mode,
            style=style,
            title=title,
            vocal_gender=vocal_gender,
            negative_tags=negative_tags,
        ),
        timeout=timeout,
    )


async def post_lyrics_task(
    base_url: str,
    api_key: str,
    *,
    prompt: str,
    timeout: float = POST_TIMEOUT_S,
) -> str:
    return await post_create_task(
        base_url,
        api_key,
        task_type="lyrics",
        input_data={"prompt": (prompt or "").strip()},
        timeout=timeout,
    )


async def post_continue_music(
    base_url: str,
    api_key: str,
    *,
    continue_clip_id: str,
    continue_at: int | float,
    prompt: str | None = None,
    timeout: float = POST_TIMEOUT_S,
) -> str:
    return await post_create_task(
        base_url,
        api_key,
        task_type="music",
        input_data=build_continue_input(
            continue_clip_id=continue_clip_id,
            continue_at=continue_at,
            prompt=prompt,
        ),
        timeout=timeout,
    )


async def post_concat_clip(
    base_url: str,
    api_key: str,
    *,
    clip_id: str,
    timeout: float = POST_TIMEOUT_S,
) -> str:
    return await post_create_task(
        base_url,
        api_key,
        task_type="concat",
        input_data={"clip_id": (clip_id or "").strip()},
        timeout=timeout,
    )


async def get_task(
    base_url: str,
    api_key: str,
    task_id: str,
    *,
    timeout: float = GET_TIMEOUT_S,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{SUNOR_TASK_PATH}/{task_id}"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                url, headers=_auth_headers(api_key), timeout=timeout
            )
        except httpx.RequestError as e:
            raise SunorClientError(
                f"Sunor get task network error: {e}",
                fallbackable=True,
            ) from e

    try:
        payload = resp.json()
    except Exception:
        payload = resp.text

    if resp.status_code != 200:
        error_code = _extract_error_code(payload)
        raise SunorClientError(
            f"Sunor get task failed: {resp.status_code}",
            status_code=resp.status_code,
            body=payload,
            error_code=error_code,
            fallbackable=resp.status_code in TRANSIENT_STATUS_CODES
            or resp.status_code >= 500
            or resp.status_code == 429,
        )

    if not isinstance(payload, dict):
        raise SunorClientError(
            "Sunor get task response not JSON object",
            body=payload,
            fallbackable=True,
        )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise SunorClientError(
            "Sunor get task missing data",
            body=payload,
            fallbackable=True,
        )
    return data


def _is_transient(exc: SunorClientError) -> bool:
    return exc.status_code is not None and exc.status_code in TRANSIENT_STATUS_CODES


async def _call_get_with_retry(
    fn: Callable[..., Awaitable[Any]],
    *args: Any,
    max_retries: int = MAX_RETRIES_TRANSIENT,
    **kwargs: Any,
) -> Any:
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return await fn(*args, **kwargs)
        except SunorClientError as e:
            if _is_transient(e) and attempt < max_retries:
                wait = (2**attempt) + random.uniform(0, 1)
                logger.warning(
                    "Sunor transient error (attempt {}/{}): status={} retrying in {:.1f}s",
                    attempt + 1,
                    max_retries + 1,
                    e.status_code,
                    wait,
                )
                await asyncio.sleep(wait)
                last_exc = e
            else:
                raise
        except httpx.ReadTimeout:
            if attempt < max_retries:
                wait = (2**attempt) + random.uniform(0, 1)
                logger.warning(
                    "Sunor ReadTimeout (attempt {}/{}), retrying in {:.1f}s",
                    attempt + 1,
                    max_retries + 1,
                    wait,
                )
                await asyncio.sleep(wait)
            else:
                raise SunorClientError("Sunor get task ReadTimeout", fallbackable=True)
    if last_exc:
        raise last_exc
    raise SunorClientError("Sunor max retries exceeded", fallbackable=True)


@dataclass(frozen=True)
class SunorTaskResult:
    task_id: str
    task_type: str
    tracks: list[MusicTrack]
    lyrics_text: str = ""


async def poll_task(
    base_url: str,
    api_key: str,
    task_id: str,
    *,
    task_type: str = "music",
    max_attempts: int,
    interval_s: float = POLL_INTERVAL_S,
    status_timeout: float = GET_TIMEOUT_S,
    progress_callback: Callable[[str, int], Awaitable[None]] | None = None,
) -> SunorTaskResult:
    for attempt in range(max_attempts):
        data = await _call_get_with_retry(
            get_task,
            base_url,
            api_key,
            task_id,
            timeout=status_timeout,
        )
        status = str(data.get("status") or "").strip().lower()
        output = data.get("output") if isinstance(data.get("output"), dict) else None
        progress = parse_progress(output.get("progress") if output else None)
        logger.debug(
            "sunor_poll task_id={} type={} status={} progress={}",
            task_id,
            task_type,
            status,
            progress,
        )
        if progress_callback is not None:
            await progress_callback(task_type, progress)

        if status == "success":
            if task_type == "lyrics":
                text = parse_lyrics_text(output)
                if not text:
                    raise SunorClientError(
                        "Sunor lyrics finished but no text parsed",
                        body={"task_id": task_id, "output": output},
                        fallbackable=True,
                    )
                return SunorTaskResult(
                    task_id=task_id,
                    task_type=task_type,
                    tracks=[],
                    lyrics_text=text,
                )
            tracks = parse_music_tracks(output)
            if not tracks:
                raise SunorClientError(
                    f"Sunor {task_type} finished but no tracks parsed",
                    body={"task_id": task_id, "output": output},
                    fallbackable=True,
                )
            return SunorTaskResult(
                task_id=task_id,
                task_type=task_type,
                tracks=tracks,
            )

        if status == "failure":
            err = data.get("error") or (output or {}).get("fail_reason") or "failure"
            raise SunorClientError(
                f"Sunor {task_type} task failure: {err}",
                body={"task_id": task_id, "data": data},
                fallbackable=True,
            )

        if status == "timeout":
            raise SunorClientError(
                f"Sunor {task_type} task timeout",
                body={"task_id": task_id},
                fallbackable=True,
            )

        await asyncio.sleep(interval_s)

    raise SunorClientError(
        f"Sunor {task_type} poll timeout",
        body={"task_id": task_id, "max_attempts": max_attempts},
        fallbackable=True,
    )


async def poll_music_task(
    base_url: str,
    api_key: str,
    task_id: str,
    *,
    max_attempts: int,
    interval_s: float = POLL_INTERVAL_S,
    status_timeout: float = GET_TIMEOUT_S,
    progress_callback: Callable[[str, int], Awaitable[None]] | None = None,
) -> list[MusicTrack]:
    result = await poll_task(
        base_url,
        api_key,
        task_id,
        task_type="music",
        max_attempts=max_attempts,
        interval_s=interval_s,
        status_timeout=status_timeout,
        progress_callback=progress_callback,
    )
    return result.tracks
