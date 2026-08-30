from __future__ import annotations

from loguru import logger

from app.domain.entities.generation_log import GenerationLog
from app.infrastructure.database.session import async_session_factory

# Approximate USD list prices (org invoice may differ slightly).
MODEL_COSTS: dict[str, dict] = {
    "gpt-5.5-mini": {"input_per_1m": 0.15, "output_per_1m": 0.60},
    "gpt-4o-mini": {"input_per_1m": 0.15, "output_per_1m": 0.60},
    "gpt-4o-mini-search-preview": {"input_per_1m": 0.15, "output_per_1m": 0.60},
    "imagen-1.5": {"per_image": {"low": 0.02, "medium": 0.04, "high": 0.08}},
    "gpt-4o-mini-tts": {"per_1m_chars": 12.0},
    "gpt-4o-tts": {"per_1m_chars": 15.0},
}

_DEFAULT_TEXT = {"input_per_1m": 0.15, "output_per_1m": 0.60}
_DEFAULT_IMAGE = 0.04
_DEFAULT_TTS_PER_1M = 12.0


def _resolve_cfg(model: str, operation: str = "") -> dict:
    if model in MODEL_COSTS:
        return MODEL_COSTS[model]
    model_l = (model or "").lower()
    op_l = (operation or "").lower()
    if "tts" in model_l or "speech" in op_l or op_l == "tts":
        return {"per_1m_chars": _DEFAULT_TTS_PER_1M}
    if (
        "imagen" in model_l
        or "dall-e" in model_l
        or "image" in model_l
        or op_l in ("image", "generate_image")
    ):
        return {"per_image": _DEFAULT_IMAGE}
    return dict(_DEFAULT_TEXT)


def estimate_cost(
    model: str,
    tokens_used: int = 0,
    operation: str = "",
    *,
    chars: int = 0,
    image_quality: str | None = None,
) -> float:
    cfg = _resolve_cfg(model, operation)

    if "per_image" in cfg:
        per = cfg["per_image"]
        if isinstance(per, dict):
            q = (image_quality or "medium").strip().lower()
            return float(per.get(q) or per.get("medium") or _DEFAULT_IMAGE)
        return float(per)

    if "per_1m_chars" in cfg:
        return (max(0, int(chars)) / 1_000_000) * float(cfg["per_1m_chars"])

    avg_rate = (float(cfg["input_per_1m"]) + float(cfg["output_per_1m"])) / 2
    return (max(0, int(tokens_used)) / 1_000_000) * avg_rate


class GenerationLogService:
    async def log(
        self,
        user_id: int,
        operation: str,
        tokens_used: int = 0,
        model: str = "gpt-5.5-mini",
        channel_id: int | None = None,
        *,
        chars: int = 0,
        image_quality: str | None = None,
    ) -> None:
        if not user_id:
            return
        cost = estimate_cost(
            model,
            tokens_used,
            operation,
            chars=chars,
            image_quality=image_quality,
        )
        # For TTS store character count in tokens_used for reporting.
        stored_tokens = int(chars) if chars and not tokens_used else int(tokens_used or 0)
        entry = GenerationLog(
            user_id=user_id,
            channel_id=channel_id,
            operation=operation,
            tokens_used=stored_tokens,
            model=model,
            estimated_cost=cost,
        )

        async with async_session_factory() as session:
            from app.infrastructure.models.generation_log import GenerationLogModel

            session.add(
                GenerationLogModel(
                    user_id=entry.user_id,
                    channel_id=entry.channel_id,
                    operation=entry.operation,
                    tokens_used=entry.tokens_used,
                    model=entry.model,
                    estimated_cost=entry.estimated_cost,
                )
            )
            await session.commit()

        logger.debug(
            f"Generation log: user={user_id} op={operation} "
            f"tokens={stored_tokens} cost=${cost:.4f}"
        )
