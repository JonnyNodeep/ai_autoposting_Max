"""Orchestration for configurable Sunor API music generation (sunor_gen block)."""
from __future__ import annotations

import asyncio
import math
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from app.application.pipeline.tale_video import download_url_to_file
from app.application.pipeline.tts_chunking import concat_audio_to_mp3
from app.config import settings
from app.infrastructure.services.openai_client import UPLOAD_DIR
from app.infrastructure.services.sunor_client import (
    MusicTrack,
    SunorClientError,
    build_custom_music_input,
    build_inspiration_input,
    build_instrumental_input,
    pick_track,
    poll_task,
    post_concat_clip,
    post_continue_music,
    post_create_task,
    post_lyrics_task,
)

ESTIMATED_CLIP_SEC = 120
MAX_EXTEND_STEPS = 8
CDN_READY_DELAY_S = 2.5


async def _await_suno_cdn_ready() -> None:
    """Brief pause after Sunor poll success before hitting the audio CDN."""
    await asyncio.sleep(CDN_READY_DELAY_S)


class SunorGenerationError(RuntimeError):
    """User-facing Sunor generation failure."""


@dataclass(frozen=True)
class SunorResult:
    path: str
    task_id: str
    clip_id: str
    image_url: str
    title: str


def _api_settings() -> tuple[str, str, int]:
    api_key = (settings.sunor.api_key or "").strip()
    if not api_key:
        raise SunorGenerationError("Сервис Sunor недоступен (нет API-ключа)")
    base_url = (settings.sunor.base_url or "https://sunor.cc/api/v1").strip()
    poll_timeout = max(60, int(settings.sunor.poll_timeout_s or 900))
    return api_key, base_url, poll_timeout


def _max_poll_attempts(poll_timeout: int) -> int:
    return max(1, int(poll_timeout / 5))


def _normalize_config(config: dict[str, Any]) -> dict[str, Any]:
    from app.application.pipeline.normalize import _normalize_sunor_gen_config

    return _normalize_sunor_gen_config(config)


def build_music_input_from_config(
    config: dict[str, Any],
    *,
    prompt_override: str | None = None,
) -> dict[str, Any]:
    cfg = _normalize_config(config)
    mode = cfg["music_mode"]
    if mode == "inspiration":
        return build_inspiration_input(
            gpt_description_prompt=str(cfg.get("gpt_description_prompt") or ""),
            make_instrumental=bool(cfg.get("make_instrumental")),
        )
    if mode == "instrumental":
        return build_instrumental_input(
            tags=str(cfg.get("tags") or ""),
            title=str(cfg.get("title") or "") or None,
            negative_tags=str(cfg.get("negative_tags") or "") or None,
        )
    prompt = (prompt_override or str(cfg.get("prompt") or "")).strip()
    return build_custom_music_input(
        prompt=prompt,
        tags=str(cfg.get("tags") or ""),
        negative_tags=str(cfg.get("negative_tags") or "") or None,
        title=str(cfg.get("title") or "") or None,
        vocal_gender=str(cfg.get("vocal_gender") or "") or None,
        make_instrumental=bool(cfg.get("make_instrumental")),
    )


async def _run_lyrics(base_url: str, api_key: str, prompt: str, max_attempts: int) -> str:
    task_id = await post_lyrics_task(base_url, api_key, prompt=prompt)
    result = await poll_task(
        base_url,
        api_key,
        task_id,
        task_type="lyrics",
        max_attempts=max_attempts,
    )
    return result.lyrics_text


async def _create_and_poll_music(
    base_url: str,
    api_key: str,
    input_data: dict[str, Any],
    *,
    max_attempts: int,
    pick_variant: str,
) -> tuple[MusicTrack, str, list[MusicTrack]]:
    task_id = await post_create_task(
        base_url,
        api_key,
        task_type="music",
        input_data=input_data,
    )
    result = await poll_task(
        base_url,
        api_key,
        task_id,
        task_type="music",
        max_attempts=max_attempts,
    )
    await _await_suno_cdn_ready()
    tracks = list(result.tracks)
    if pick_variant == "first_ok":
        last_exc: Exception | None = None
        for track in tracks:
            try:
                UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
                probe = UPLOAD_DIR / f"sunor_probe_{uuid.uuid4().hex[:8]}.mp3"
                await download_url_to_file(track.audio_url, probe)
                probe.unlink(missing_ok=True)
                return track, task_id, tracks
            except Exception as exc:
                last_exc = exc
                logger.warning("Sunor track variant {} download failed: {}", track.variant_index, exc)
        if last_exc:
            raise SunorGenerationError("Не удалось скачать аудио Sunor") from last_exc
    track = pick_track(tracks, pick_variant)
    return track, task_id, tracks


async def _maybe_extend_track(
    base_url: str,
    api_key: str,
    track: MusicTrack,
    config: dict[str, Any],
    *,
    max_attempts: int,
    pick_variant: str,
) -> tuple[MusicTrack, str, list[MusicTrack]]:
    cfg = _normalize_config(config)
    if not cfg["extend_enabled"] or cfg["target_duration_sec"] <= 0:
        return track, "", [track]

    target = cfg["target_duration_sec"]
    steps_needed = max(0, math.ceil(target / ESTIMATED_CLIP_SEC) - 1)
    steps_needed = min(steps_needed, MAX_EXTEND_STEPS)
    if steps_needed == 0:
        return track, "", [track]

    current = track
    last_task_id = ""
    last_tracks: list[MusicTrack] = [track]
    continue_prompt = str(cfg.get("continue_prompt") or "").strip()
    continue_at = cfg["continue_at_sec"]

    for step in range(steps_needed):
        if not current.audio_id:
            break
        logger.info(
            "Sunor extend step {}/{} clip_id={} continue_at={}",
            step + 1,
            steps_needed,
            current.audio_id,
            continue_at,
        )
        task_id = await post_continue_music(
            base_url,
            api_key,
            continue_clip_id=current.audio_id,
            continue_at=continue_at,
            prompt=continue_prompt or None,
        )
        result = await poll_task(
            base_url,
            api_key,
            task_id,
            task_type="music",
            max_attempts=max_attempts,
        )
        current = pick_track(result.tracks, pick_variant)
        last_tracks = list(result.tracks)
        last_task_id = task_id

    if current.audio_id and steps_needed > 0:
        try:
            concat_task_id = await post_concat_clip(
                base_url,
                api_key,
                clip_id=current.audio_id,
            )
            concat_result = await poll_task(
                base_url,
                api_key,
                concat_task_id,
                task_type="concat",
                max_attempts=max_attempts,
            )
            last_tracks = list(concat_result.tracks)
            current = pick_track(last_tracks, pick_variant)
            last_task_id = concat_task_id
        except SunorClientError as exc:
            logger.warning("Sunor concat skipped: {}", exc)

    return current, last_task_id, last_tracks


async def _download_tracks_with_fallback(
    tracks: list[MusicTrack],
    *,
    preferred: MusicTrack | None = None,
) -> tuple[str, MusicTrack]:
    await _await_suno_cdn_ready()
    ordered: list[MusicTrack] = []
    if preferred is not None:
        ordered.append(preferred)
    seen_ids: set[str] = {preferred.audio_id} if preferred and preferred.audio_id else set()
    for track in tracks:
        if track.audio_id and track.audio_id in seen_ids:
            continue
        if preferred and track.audio_url == preferred.audio_url:
            continue
        ordered.append(track)
        if track.audio_id:
            seen_ids.add(track.audio_id)

    if not ordered:
        raise SunorGenerationError("Sunor не вернул аудио дорожки")

    last_exc: Exception | None = None
    for idx, track in enumerate(ordered, start=1):
        try:
            path = await _download_track(track)
            return path, track
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "Sunor audio download failed track={}/{}: {}",
                idx,
                len(ordered),
                exc,
            )
    raise SunorGenerationError("Не удалось скачать аудио Sunor") from last_exc


async def _download_track(track: MusicTrack) -> str:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    out = UPLOAD_DIR / f"sunor_gen_{uuid.uuid4().hex[:12]}.mp3"
    await download_url_to_file(track.audio_url, out)
    return str(out)


async def _download_all_tracks(tracks: list[MusicTrack]) -> list[Path]:
    await _await_suno_cdn_ready()
    paths: list[Path] = []
    for idx, track in enumerate(tracks, start=1):
        if not (track.audio_url or "").strip():
            continue
        out = UPLOAD_DIR / f"sunor_part_{uuid.uuid4().hex[:10]}_{idx}.mp3"
        await download_url_to_file(track.audio_url, out)
        paths.append(out)
    if not paths:
        raise SunorGenerationError("Sunor не вернул аудио дорожки")
    return paths


async def _generate_merge_4tracks(
    config: dict[str, Any],
    *,
    on_progress: Any | None = None,
) -> SunorResult:
    cfg = _normalize_config(config)
    api_key, base_url, poll_timeout = _api_settings()
    max_attempts = _max_poll_attempts(poll_timeout)
    fade_ms = int(cfg.get("merge_crossfade_ms") or 5000)

    if on_progress:
        await on_progress("🎵 Sunor: генерирую 2 партии для склейки…")

    all_parts: list[Path] = []
    last_task_id = ""
    title = str(cfg.get("title") or "").strip()

    for batch in range(2):
        input_data = build_music_input_from_config(cfg)
        track, task_id, batch_tracks = await _create_and_poll_music(
            base_url,
            api_key,
            input_data,
            max_attempts=max_attempts,
            pick_variant="first",
        )
        last_task_id = task_id
        if len(batch_tracks) >= 2:
            parts = await _download_all_tracks(batch_tracks[:2])
        else:
            parts = await _download_all_tracks([track])
        all_parts.extend(parts)

    if len(all_parts) < 2:
        raise SunorGenerationError("Недостаточно дорожек для склейки")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    out = UPLOAD_DIR / f"sunor_merge_{uuid.uuid4().hex[:12]}.mp3"
    concat_audio_to_mp3(all_parts, out, fade_ms=fade_ms)
    for part in all_parts:
        part.unlink(missing_ok=True)

    logger.info(
        "Sunor merge_4tracks done parts={} fade_ms={} out={}",
        len(all_parts),
        fade_ms,
        out,
    )
    return SunorResult(
        path=str(out),
        task_id=last_task_id,
        clip_id="",
        image_url="",
        title=title,
    )


async def generate_sunor_track(
    config: dict[str, Any],
    *,
    story_script: str = "",
    audio_script: str = "",
    topic_title: str = "",
    on_progress: Any | None = None,
) -> SunorResult:
    """Main entry: lyrics (optional) → music → extend/merge → download."""
    cfg = _normalize_config(config)
    if topic_title:
        cfg["title"] = topic_title[:120]
    gen_mode = str(cfg.get("generation_mode") or "single").strip().lower()
    if gen_mode == "merge_4tracks":
        return await _generate_merge_4tracks(cfg, on_progress=on_progress)

    api_key, base_url, poll_timeout = _api_settings()
    max_attempts = _max_poll_attempts(poll_timeout)

    prompt_override: str | None = None
    if (audio_script or "").strip():
        prompt_override = audio_script.strip()
    elif cfg["prompt_source"] == "story_gen" and (story_script or "").strip():
        prompt_override = story_script.strip()
    elif cfg["lyrics_enabled"]:
        lyrics_prompt = str(cfg.get("lyrics_prompt") or "").strip()
        if not lyrics_prompt and cfg["music_mode"] == "custom":
            lyrics_prompt = str(cfg.get("prompt") or "").strip()
        if lyrics_prompt:
            if on_progress:
                await on_progress("🎵 Sunor: генерирую текст…")
            try:
                prompt_override = await _run_lyrics(
                    base_url, api_key, lyrics_prompt, max_attempts
                )
            except SunorClientError as exc:
                raise SunorGenerationError(
                    "Не удалось сгенерировать текст через Sunor Lyrics"
                ) from exc

    input_data = build_music_input_from_config(cfg, prompt_override=prompt_override)
    mode = cfg["music_mode"]
    if mode == "instrumental" and not input_data.get("tags"):
        raise SunorGenerationError("Для инструментала нужны tags (стиль)")
    if mode == "inspiration" and not input_data.get("gpt_description_prompt"):
        raise SunorGenerationError("Нужно описание для режима Inspiration")
    if mode == "custom" and not input_data.get("prompt") and not input_data.get("tags"):
        raise SunorGenerationError("Нужен prompt или tags для режима Custom")

    if on_progress:
        await on_progress("🎵 Sunor: генерирую музыку…")

    try:
        track, task_id, candidate_tracks = await _create_and_poll_music(
            base_url,
            api_key,
            input_data,
            max_attempts=max_attempts,
            pick_variant=cfg["pick_variant"],
        )
        if cfg["extend_enabled"] and cfg["target_duration_sec"] > 0:
            if on_progress:
                await on_progress("🎵 Sunor: удлиняю трек…")
            track, extend_task_id, candidate_tracks = await _maybe_extend_track(
                base_url,
                api_key,
                track,
                cfg,
                max_attempts=max_attempts,
                pick_variant=cfg["pick_variant"],
            )
            if extend_task_id:
                task_id = extend_task_id
        path, track = await _download_tracks_with_fallback(
            candidate_tracks,
            preferred=track,
        )
    except SunorClientError as exc:
        logger.error("Sunor generation failed: {}", exc)
        raise SunorGenerationError(
            "Не удалось сгенерировать трек через Sunor. Попробуйте позже."
        ) from exc

    image_url = track.image_url if cfg["attach_cover_image"] else ""
    result_title = topic_title or track.title
    return SunorResult(
        path=path,
        task_id=task_id,
        clip_id=track.audio_id,
        image_url=image_url,
        title=result_title,
    )
