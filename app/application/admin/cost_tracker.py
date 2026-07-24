from loguru import logger

from app.domain.entities.generation_log import GenerationLog
from app.infrastructure.database.session import async_session_factory
from app.domain.value_objects.subscription_status import SubscriptionStatus


MODEL_COSTS = {
    "gpt-5.5-mini": {"input_per_1m": 0.15, "output_per_1m": 0.60},
    "imagen-1.5": {"per_image": 0.04},
}


def estimate_cost(model: str, tokens_used: int, operation: str = "") -> float:
    if model not in MODEL_COSTS:
        return 0.0

    cfg = MODEL_COSTS[model]
    if "per_image" in cfg:
        return cfg["per_image"]
    avg_rate = (cfg["input_per_1m"] + cfg["output_per_1m"]) / 2
    return (tokens_used / 1_000_000) * avg_rate


class GenerationLogService:
    async def log(
        self,
        user_id: int,
        operation: str,
        tokens_used: int = 0,
        model: str = "gpt-5.5-mini",
        channel_id: int | None = None,
    ) -> None:
        cost = estimate_cost(model, tokens_used, operation)
        entry = GenerationLog(
            user_id=user_id,
            channel_id=channel_id,
            operation=operation,
            tokens_used=tokens_used,
            model=model,
            estimated_cost=cost,
        )

        async with async_session_factory() as session:
            from app.infrastructure.models.generation_log import GenerationLogModel
            session.add(GenerationLogModel(
                user_id=entry.user_id,
                channel_id=entry.channel_id,
                operation=entry.operation,
                tokens_used=entry.tokens_used,
                model=entry.model,
                estimated_cost=entry.estimated_cost,
            ))
            await session.commit()

        logger.debug(f"Generation log: user={user_id} op={operation} tokens={tokens_used} cost=${cost:.4f}")
