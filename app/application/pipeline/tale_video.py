"""Fairy-tale video pipeline: GPT script+scenes, Sunor narration, images, MP4."""
from __future__ import annotations

import asyncio
import base64
import json
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx
from loguru import logger
from openai import AsyncOpenAI

from app.application.pipeline.tale_prompts import (
    FIXED_TALE_AGE,
    FIXED_TALE_MOOD,
    FIXED_TALE_STYLE,
    SCENES_MAX,
    SCENES_MIN,
    STORY_TARGET_CHARS,
    build_story_shorten_user_prompt,
    build_story_system_prompt,
    build_story_user_prompt,
    build_sunor_negative_tags,
    build_sunor_tags,
    finalize_scene_image_prompt,
    wrap_story_for_sunor,
)
from app.config import settings
from app.infrastructure.services.openai_client import UPLOAD_DIR
from app.infrastructure.services.sunor_client import (
    MusicTrack,
    SunorClientError,
    poll_music_task,
    post_create_music_task,
)
from app.infrastructure.services.tale_slideshow import build_slideshow_mp4

_JSON_FIELD_RE = re.compile(
    r'"(title|caption|story)"\s*:\s*"((?:[^"\\]|\\.)*)"',
    re.DOTALL,
)
IMAGE_CONCURRENCY = 3
MAX_STORY_LLM_ATTEMPTS = 2
OPENAI_IMAGES_URL = "https://api.openai.com/v1/images/generations"


@dataclass(frozen=True)
class TaleScene:
    id: int
    story_span: str
    image_prompt_en: str
    hero_in_scene: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaleScene:
        return cls(
            id=int(data.get("id") or 0),
            story_span=str(data.get("story_span") or ""),
            image_prompt_en=str(data.get("image_prompt_en") or ""),
            hero_in_scene=bool(data.get("hero_in_scene")),
        )


@dataclass(frozen=True)
class TaleScript:
    title: str
    caption: str
    story: str
    scenes: list[TaleScene]

    def to_meta(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "caption": self.caption,
            "story": self.story,
            "scenes": [s.to_dict() for s in self.scenes],
        }

    @classmethod
    def from_meta(cls, data: dict[str, Any] | None) -> TaleScript | None:
        if not isinstance(data, dict):
            return None
        story = str(data.get("story") or "").strip()
        if not story:
            return None
        scenes_raw = data.get("scenes") or []
        scenes: list[TaleScene] = []
        if isinstance(scenes_raw, list):
            for item in scenes_raw:
                if isinstance(item, dict):
                    scenes.append(TaleScene.from_dict(item))
        return cls(
            title=str(data.get("title") or "Сказка")[:120],
            caption=str(data.get("caption") or "")[:500],
            story=story,
            scenes=scenes,
        )


@dataclass(frozen=True)
class TaleVideoResult:
    title: str
    caption: str
    story: str
    video_path: str
    audio_path: str
    sunor_task_id: str
    scene_count: int


class TaleGenerationError(RuntimeError):
    """User-facing fairy-tale generation failure."""


def _strip_code_fences(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, count=1, flags=re.I)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _unescape_json_string(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except json.JSONDecodeError:
        return (
            value.replace(r"\"", '"')
            .replace(r"\n", "\n")
            .replace(r"\t", "\t")
            .replace(r"\\", "\\")
        )


def _extract_json_object(raw: str) -> dict[str, Any] | None:
    text = _strip_code_fences(raw or "")
    if not text:
        return None
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return None


def _looks_like_json_blob(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    head = t[:80].lstrip()
    return head.startswith("{") or head.startswith("```") or '"story"' in head


def truncate_story(story: str, max_chars: int = STORY_TARGET_CHARS) -> str:
    """Trim story to max_chars, preferring a sentence or word boundary."""
    text = (story or "").strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    min_keep = int(max_chars * 0.75)
    for sep in (". ", ".\n", "! ", "? ", "… ", "; "):
        idx = cut.rfind(sep)
        if idx >= min_keep:
            return cut[: idx + 1].strip()
    idx = cut.rfind(" ")
    if idx >= int(max_chars * 0.85):
        return cut[:idx].strip()
    return cut.strip()


def scenes_from_story(story: str, *, n: int = SCENES_MIN) -> list[TaleScene]:
    """Split story into N scene spans for slideshow."""
    text = (story or "").strip()
    if not text:
        return []
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paras) >= n:
        groups: list[list[str]] = [[] for _ in range(n)]
        for i, p in enumerate(paras):
            groups[min(n - 1, i * n // len(paras))].append(p)
        chunks = ["\n\n".join(g) for g in groups if g]
    else:
        size = max(1, len(text) // n)
        chunks = []
        for i in range(n):
            start = i * size
            end = len(text) if i == n - 1 else (i + 1) * size
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
    return [
        TaleScene(
            id=i + 1,
            story_span=chunk,
            image_prompt_en=f"Storybook scene {i + 1} illustrating the fairy tale moment",
        )
        for i, chunk in enumerate(chunks[:SCENES_MAX])
    ]


def _fallback_scenes_from_story(story: str, *, n: int = SCENES_MIN) -> list[TaleScene]:
    return scenes_from_story(story, n=n)


def _parse_scenes(raw_scenes: Any, story: str) -> list[TaleScene]:
    if not isinstance(raw_scenes, list):
        return []
    scenes: list[TaleScene] = []
    for i, item in enumerate(raw_scenes):
        if not isinstance(item, dict):
            continue
        span = str(item.get("story_span") or "").strip()
        prompt = str(item.get("image_prompt_en") or item.get("image_prompt") or "").strip()
        if not span:
            continue
        sid = item.get("id")
        try:
            scene_id = int(sid) if sid is not None else i + 1
        except (TypeError, ValueError):
            scene_id = i + 1
        scenes.append(
            TaleScene(id=scene_id, story_span=span, image_prompt_en=prompt)
        )
    if SCENES_MIN <= len(scenes) <= SCENES_MAX:
        return scenes
    return _fallback_scenes_from_story(story, n=SCENES_MIN)


def parse_tale_script(raw: str) -> TaleScript:
    data = _extract_json_object(raw) or {}
    story = str(data.get("story") or "").strip()
    if _looks_like_json_blob(story):
        story = ""
    if not story:
        fields = {
            m.group(1): _unescape_json_string(m.group(2)).strip()
            for m in _JSON_FIELD_RE.finditer(raw or "")
        }
        story = (fields.get("story") or "").strip()
    if not story:
        raise TaleGenerationError("Не удалось сгенерировать текст сказки")

    title = str(data.get("title") or "").strip()
    caption = str(data.get("caption") or "").strip()
    if not title:
        title = (caption or story.split("\n", 1)[0])[:80] or "Сказка"
    if not caption or _looks_like_json_blob(caption):
        caption = story.split("\n", 1)[0].strip()[:300]

    scenes = _parse_scenes(data.get("scenes"), story)
    if len(scenes) < SCENES_MIN:
        scenes = _fallback_scenes_from_story(story)
    if not scenes:
        raise TaleGenerationError("Не удалось построить сцены сказки")
    return TaleScript(title=title[:120], caption=caption[:500], story=story, scenes=scenes)


def apply_story_length_limit(script: TaleScript) -> TaleScript:
    if len(script.story) <= STORY_TARGET_CHARS:
        return script
    original_len = len(script.story)
    story = truncate_story(script.story, STORY_TARGET_CHARS)
    logger.error(
        "Tale story still over limit after {} LLM attempts ({} chars); "
        "last-resort truncate to {} chars",
        MAX_STORY_LLM_ATTEMPTS,
        original_len,
        len(story),
    )
    scene_count = (
        len(script.scenes)
        if SCENES_MIN <= len(script.scenes) <= SCENES_MAX
        else SCENES_MIN
    )
    scenes = _fallback_scenes_from_story(story, n=scene_count)
    return TaleScript(
        title=script.title,
        caption=script.caption,
        story=story,
        scenes=scenes,
    )


async def _llm_chat(messages: list[dict[str, str]], *, model: str) -> str:
    client = AsyncOpenAI(api_key=settings.openai.api_key)
    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.8,
        timeout=180.0,
    )
    return response.choices[0].message.content or ""


async def generate_tale_script(
    *,
    topic: str,
    style: str = FIXED_TALE_STYLE,
    mood: str = FIXED_TALE_MOOD,
    age: str = FIXED_TALE_AGE,
) -> TaleScript:
    model = (settings.openai.tale_model or "gpt-5.4").strip() or "gpt-5.4"
    system_prompt = build_story_system_prompt(style=style, mood=mood, age=age)
    user_prompt = build_story_user_prompt(
        topic=topic, style=style, mood=mood, age=age
    )
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    script: TaleScript | None = None
    for attempt in range(1, MAX_STORY_LLM_ATTEMPTS + 1):
        raw = await _llm_chat(messages, model=model)
        script = parse_tale_script(raw)
        if len(script.story) <= STORY_TARGET_CHARS:
            break
        if attempt >= MAX_STORY_LLM_ATTEMPTS:
            break
        logger.warning(
            "Tale story too long ({} chars, max {}), LLM rewrite attempt {}/{}",
            len(script.story),
            STORY_TARGET_CHARS,
            attempt + 1,
            MAX_STORY_LLM_ATTEMPTS,
        )
        messages.append(
            {
                "role": "user",
                "content": build_story_shorten_user_prompt(
                    topic=topic,
                    title=script.title,
                    story_len=len(script.story),
                ),
            }
        )

    assert script is not None
    script = apply_story_length_limit(script)
    logger.info(
        "Tale script ready title={!r} story_len={} scenes={} (max {}) model={}",
        script.title,
        len(script.story),
        len(script.scenes),
        STORY_TARGET_CHARS,
        model,
    )
    return script


_CDN_DOWNLOAD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "audio/mpeg,audio/*,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://suno.com/",
}
_CDN_RETRYABLE_STATUS = frozenset({403, 429, 500, 502, 503, 504})
_CDN_NETWORK_ERRORS = (
    httpx.ConnectTimeout,
    httpx.ConnectError,
    httpx.ProxyError,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
)


def _pick_primary_track(tracks: list[MusicTrack]) -> MusicTrack:
    if not tracks:
        raise TaleGenerationError("Sunor не вернул аудио дорожки")
    for t in tracks:
        if t.audio_url.startswith("http"):
            return t
    return tracks[0]


def _http_audio_urls(tracks: list[MusicTrack]) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for t in tracks:
        url = (t.audio_url or "").strip()
        if url.startswith("http") and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def _cdn_host_alternates(url: str) -> list[str]:
    """Prefer original URL, then cdn1 <-> cdn2 swap on the same path."""
    out = [url]
    if "cdn1.suno.ai" in url:
        alt = url.replace("cdn1.suno.ai", "cdn2.suno.ai", 1)
        if alt != url:
            out.append(alt)
    elif "cdn2.suno.ai" in url:
        alt = url.replace("cdn2.suno.ai", "cdn1.suno.ai", 1)
        if alt != url:
            out.append(alt)
    return out


def _cdn_download_proxy() -> str | None:
    for raw in (
        getattr(settings.yandex, "tts_proxy", "") or "",
        getattr(settings.rss, "http_proxy", "") or "",
    ):
        proxy = str(raw).strip()
        if proxy:
            return proxy
    return None


def _format_download_error(exc: BaseException) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code if exc.response is not None else "?"
        return f"HTTP {status}"
    return type(exc).__name__


async def download_url_to_file(
    url: str,
    dest: Path,
    *,
    timeout: float = 180.0,
    attempts: int = 3,
) -> Path:
    """Download CDN audio with browser UA, optional proxy, and retries."""
    proxy = _cdn_download_proxy()
    last_exc: BaseException | None = None
    candidate_urls = _cdn_host_alternates(url)

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        proxy=proxy,
        headers=_CDN_DOWNLOAD_HEADERS,
    ) as client:
        for candidate in candidate_urls:
            for attempt in range(1, attempts + 1):
                try:
                    resp = await client.get(candidate)
                    if resp.status_code in _CDN_RETRYABLE_STATUS:
                        host = httpx.URL(candidate).host or "?"
                        logger.warning(
                            "CDN download retryable status={} host={} "
                            "attempt={}/{} url={}",
                            resp.status_code,
                            host,
                            attempt,
                            attempts,
                            candidate[:120],
                        )
                        last_exc = httpx.HTTPStatusError(
                            f"HTTP {resp.status_code}",
                            request=resp.request,
                            response=resp,
                        )
                        if attempt < attempts:
                            await asyncio.sleep(min(2 ** (attempt - 1), 4))
                            continue
                        break
                    resp.raise_for_status()
                    dest.write_bytes(resp.content)
                    return dest
                except _CDN_NETWORK_ERRORS as exc:
                    host = httpx.URL(candidate).host or "?"
                    logger.warning(
                        "CDN download network error host={} attempt={}/{}: {}",
                        host,
                        attempt,
                        attempts,
                        exc,
                    )
                    last_exc = exc
                    if attempt < attempts:
                        await asyncio.sleep(min(2 ** (attempt - 1), 4))
                        continue
                    break
                except httpx.HTTPStatusError as exc:
                    last_exc = exc
                    status = exc.response.status_code if exc.response is not None else None
                    if status in _CDN_RETRYABLE_STATUS and attempt < attempts:
                        await asyncio.sleep(min(2 ** (attempt - 1), 4))
                        continue
                    break

    assert last_exc is not None
    raise last_exc


async def synthesize_tale_audio_sunor(
    *,
    story: str,
    title: str,
    style: str = FIXED_TALE_STYLE,
    mood: str = FIXED_TALE_MOOD,
    age: str = FIXED_TALE_AGE,
) -> tuple[str, str]:
    api_key = (settings.sunor.api_key or "").strip()
    if not api_key:
        raise TaleGenerationError("Сервис озвучки временно недоступен (нет ключа Sunor)")
    base_url = (settings.sunor.base_url or "https://sunor.cc/api/v1").strip()
    poll_timeout = max(60, int(settings.sunor.poll_timeout_s or 900))
    max_attempts = max(1, int(poll_timeout / 5))

    tags = build_sunor_tags(style, mood, age)
    negative = build_sunor_negative_tags()
    if len(story) > STORY_TARGET_CHARS:
        logger.warning(
            "Tale story exceeds limit at Sunor step ({} chars); trimming",
            len(story),
        )
        story = truncate_story(story, STORY_TARGET_CHARS)
    prompt = wrap_story_for_sunor(story)

    try:
        task_id = await post_create_music_task(
            base_url,
            api_key,
            prompt=prompt,
            instrumental=False,
            custom_mode=True,
            style=tags,
            title=title[:80] or "Сказка",
            negative_tags=negative,
            timeout=120.0,
        )
        tracks = await poll_music_task(
            base_url,
            api_key,
            task_id,
            max_attempts=max_attempts,
            interval_s=5.0,
            status_timeout=60.0,
        )
    except SunorClientError as exc:
        raise TaleGenerationError(
            "Не удалось озвучить сказку. Попробуйте позже или измените тему."
        ) from exc

    urls = _http_audio_urls(tracks)
    if not urls:
        raise TaleGenerationError("Sunor не вернул аудио дорожки")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    audio_path = UPLOAD_DIR / f"tale_audio_{uuid.uuid4().hex[:12]}.mp3"
    last_exc: BaseException | None = None
    for idx, audio_url in enumerate(urls, start=1):
        try:
            await download_url_to_file(audio_url, audio_path)
            return str(audio_path), task_id
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "Tale audio download failed track={}/{} detail={}: {}",
                idx,
                len(urls),
                _format_download_error(exc),
                exc,
            )

    detail = _format_download_error(last_exc) if last_exc else "unknown"
    raise TaleGenerationError(
        f"Не удалось скачать озвучку сказки ({detail})"
    ) from last_exc


async def _generate_scene_image_bytes(prompt: str) -> bytes:
    api_key = (settings.openai.api_key or "").strip()
    if not api_key:
        raise TaleGenerationError("OPENAI_API_KEY не настроен")
    model = (settings.openai.image_model or "gpt-image-2").strip()
    size = (settings.openai.tale_image_size or "1536x1024").strip() or "1536x1024"
    quality = (settings.openai.tale_image_quality or "low").strip() or "low"
    payload = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": size,
        "quality": quality,
    }
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(
            OPENAI_IMAGES_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    if resp.status_code != 200:
        raise TaleGenerationError(
            f"Не удалось сгенерировать иллюстрацию сцены ({resp.status_code})"
        )
    data = resp.json()
    image_data = (data.get("data") or [{}])[0]
    b64_json = image_data.get("b64_json")
    if b64_json:
        return base64.b64decode(b64_json)
    url = image_data.get("url")
    if isinstance(url, str) and url.startswith("http"):
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            return r.content
    raise TaleGenerationError("Пустой ответ OpenAI Images")


async def generate_scene_images(
    script: TaleScript, *, style: str = FIXED_TALE_STYLE
) -> list[Path]:
    sem = asyncio.Semaphore(IMAGE_CONCURRENCY)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    async def one(scene: TaleScene, idx: int) -> Path:
        async with sem:
            prompt = finalize_scene_image_prompt(scene.image_prompt_en, style=style)
            image_bytes = await _generate_scene_image_bytes(prompt)
        path = UPLOAD_DIR / f"tale_s{idx}_{uuid.uuid4().hex[:10]}.png"
        path.write_bytes(image_bytes)
        return path

    paths = await asyncio.gather(
        *[one(scene, i) for i, scene in enumerate(script.scenes)]
    )
    return list(paths)


async def build_tale_video_from_script(script: TaleScript) -> TaleVideoResult:
    """Sunor audio + scene images + slideshow → local MP4."""
    if len(script.story) > STORY_TARGET_CHARS:
        script = apply_story_length_limit(script)

    audio_path, task_id = await synthesize_tale_audio_sunor(
        story=script.story,
        title=script.title,
    )
    image_paths: list[Path] = []
    video_path: Path | None = None
    try:
        image_paths = await generate_scene_images(script)
        span_lengths = [max(1, len(s.story_span)) for s in script.scenes]
        out = UPLOAD_DIR / f"tale_video_{uuid.uuid4().hex[:12]}.mp4"
        video_path = build_slideshow_mp4(
            image_paths=image_paths,
            audio_path=Path(audio_path),
            span_lengths=span_lengths,
            output_path=out,
        )
    except Exception as exc:
        Path(audio_path).unlink(missing_ok=True)
        for p in image_paths:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        if isinstance(exc, TaleGenerationError):
            raise
        raise TaleGenerationError(
            "Не удалось собрать видео-сказку. Попробуйте позже."
        ) from exc
    finally:
        for p in image_paths:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass

    return TaleVideoResult(
        title=script.title,
        caption=script.caption,
        story=script.story,
        video_path=str(video_path),
        audio_path=audio_path,
        sunor_task_id=task_id,
        scene_count=len(script.scenes),
    )
