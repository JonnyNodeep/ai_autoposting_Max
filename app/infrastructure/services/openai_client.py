import base64
import uuid
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from app.config import settings
from app.domain.interfaces.openai_client import OpenAIClient
from loguru import logger

UPLOAD_DIR = Path(__file__).parent.parent.parent.parent / "uploads"
WATERMARK_SCALE = 3


class OpenAIService(OpenAIClient):
    def __init__(self) -> None:
        self._client = AsyncOpenAI(api_key=settings.openai.api_key)
        self._text_model = settings.openai.text_model
        self._image_model = settings.openai.image_model

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

    async def generate_image(self, prompt: str, channel_link: str | None = None) -> str:
        response = await self._client.images.generate(
            model=self._image_model,
            prompt=prompt,
            n=1,
            size="1024x1024",
        )
        image_data = response.data[0]
        if image_data.url:
            return image_data.url
        if image_data.b64_json:
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            filename = f"logo_{uuid.uuid4().hex[:12]}.png"
            filepath = UPLOAD_DIR / filename
            filepath.write_bytes(base64.b64decode(image_data.b64_json))
            if channel_link:
                slug = channel_link.rstrip("/").split("/")[-1]
                _apply_watermark(str(filepath), slug)
            return str(filepath)
        return ""

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


def _apply_watermark(filepath: str, slug: str) -> None:
    from PIL import Image, ImageDraw, ImageFont
    img = Image.open(filepath).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            72 * WATERMARK_SCALE,
        )
    except OSError:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), slug, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    margin = 30 * WATERMARK_SCALE
    pad_x = 15 * WATERMARK_SCALE
    pad_y = 10 * WATERMARK_SCALE
    x = img.width - tw - margin
    y = img.height - th - margin
    draw.rectangle([x - pad_x, y - pad_y, x + tw + pad_x, y + th + pad_y], fill=(0, 0, 0, 100))
    draw.text((x, y), slug, font=font, fill=(255, 255, 255, 200))
    combined = Image.alpha_composite(img, overlay)
    combined.save(filepath, "PNG")


def _apply_video_watermark(input_path: str, output_path: str, slug: str) -> None:
    import subprocess

    escaped = slug.replace(":", "\\:").replace("'", "\\'")
    drawtext = (
        f"drawtext=text='{escaped}':"
        f"fontsize=9:"
        f"fontcolor=white@0.8:"
        f"box=1:boxcolor=black@0.4:boxborderw=5:"
        f"x=w-tw-10:y=h-th-10"
    )

    result = subprocess.run(
        [
            "ffmpeg", "-i", input_path,
            "-vf", drawtext,
            "-codec:a", "copy",
            "-y", output_path,
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        logger.error(f"ffmpeg watermark failed: {result.stderr[:300]}")
        raise RuntimeError(f"Video watermark failed")
    logger.info(f"Video watermarked: {input_path} -> {output_path}")
