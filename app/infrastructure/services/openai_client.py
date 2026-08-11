import base64
import uuid
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from app.config import settings
from app.domain.interfaces.openai_client import OpenAIClient
from loguru import logger

UPLOAD_DIR = Path(__file__).parent.parent.parent.parent / "uploads"


class OpenAIService(OpenAIClient):
    def __init__(self) -> None:
        self._client = AsyncOpenAI(api_key=settings.openai.api_key)
        self._text_model = settings.openai.text_model
        self._image_model = settings.openai.image_model
        self._image_quality = settings.openai.image_quality

    async def generate_text(
        self, prompt: str, system_prompt: str | None = None
    ) -> str:
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        response = await self._client.chat.completions.create(
            model=self._text_model,
            messages=messages,
            temperature=0.8,
        )
        return response.choices[0].message.content or ""

    async def generate_image(self, prompt: str) -> str:
        response = await self._client.images.generate(
            model=self._image_model,
            prompt=prompt,
            n=1,
            size="1024x1024",
            quality=self._image_quality,
        )
        image_data = response.data[0]
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"logo_{uuid.uuid4().hex[:12]}.png"
        filepath = UPLOAD_DIR / filename

        if image_data.b64_json:
            filepath.write_bytes(base64.b64decode(image_data.b64_json))
        elif image_data.url:
            import httpx

            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.get(image_data.url)
                resp.raise_for_status()
                filepath.write_bytes(resp.content)
        else:
            return ""

        return str(filepath)

    async def analyze_vision(self, prompt: str, base64_images: list[str]) -> str:
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for img in base64_images[:5]:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{img}"},
            })
        response = await self._client.chat.completions.create(
            model=self._text_model,
            messages=[{"role": "user", "content": content}],
            max_completion_tokens=500,
        )
        return response.choices[0].message.content or ""

    async def search_web(self, query: str) -> str:
        search_model = settings.openai.search_model
        response = await self._client.chat.completions.create(
            model=search_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты — исследователь. Найди в интернете актуальную информацию "
                        "по запросу пользователя. Собери ключевые факты, цифры, даты. "
                        "Укажи источники в формате [Источник: URL]. "
                        "Отвечай только фактами из поиска, ничего не выдумывай. "
                        "Пиши на русском языке."
                    ),
                },
                {"role": "user", "content": query},
            ],
            web_search_options={"search_context_size": "medium"},
            max_completion_tokens=2000,
        )
        content = response.choices[0].message.content or ""

        annotations = getattr(response.choices[0].message, "annotations", []) or []
        if annotations:
            urls = []
            for a in annotations:
                if hasattr(a, "url_citation") and a.url_citation:
                    urls.append(f"[Источник: {a.url_citation.url}]")
            if urls and "Источник:" not in content:
                content += "\n\n" + "\n".join(urls)

        logger.info(f"Web search completed for: {query[:80]}")
        return content

    async def generate_speech(
        self,
        text: str,
        *,
        model: str | None = None,
        voice: str = "shimmer",
        speed: float = 0.85,
        response_format: str = "mp3",
        instructions: str | None = None,
    ) -> str:
        from app.application.pipeline.tts_chunking import (
            chunk_tts_text,
            concat_audio_to_mp3,
            max_chars_for_model,
        )

        script = (text or "").strip()
        if not script:
            raise ValueError("Empty text for speech generation")

        tts_model = (model or settings.openai.tts_model or "gpt-4o-mini-tts").strip()
        speed_val = max(0.25, min(4.0, float(speed)))
        # Always request lossless WAV from API; deliver MP3 320k for publish.
        # response_format arg kept for API compat but ignored for delivery.
        _ = response_format
        api_fmt = "wav"
        style = (instructions or "").strip() or None
        use_instructions = bool(style) and (
            tts_model.startswith("gpt-4o-mini-tts") or "mini-tts" in tts_model.lower()
        )
        chunks = chunk_tts_text(script, max_chars=max_chars_for_model(tts_model))
        if not chunks:
            raise ValueError("Empty text for speech generation")

        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        part_paths: list[Path] = []
        try:
            for i, chunk in enumerate(chunks):
                kwargs: dict = {
                    "model": tts_model,
                    "voice": voice,
                    "input": chunk,
                    "response_format": api_fmt,
                    "speed": speed_val,
                }
                if use_instructions:
                    kwargs["instructions"] = style
                response = await self._client.audio.speech.create(**kwargs)
                part_path = UPLOAD_DIR / f"tts_part_{uuid.uuid4().hex[:12]}_{i}.{api_fmt}"
                part_path.write_bytes(response.content)
                part_paths.append(part_path)

            out_path = UPLOAD_DIR / f"tts_{uuid.uuid4().hex[:12]}.mp3"
            concat_audio_to_mp3(part_paths, out_path)
            logger.info(
                f"TTS done model={tts_model} voice={voice} "
                f"chunks={len(chunks)} api=wav deliver=mp3_320k path={out_path}"
            )
            return str(out_path)
        finally:
            for p in part_paths:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
