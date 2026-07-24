from app.domain.value_objects.subscription_tier import SubscriptionTier
from app.domain.value_objects.subscription_status import SubscriptionStatus
from app.domain.interfaces.subscription_repository import SubscriptionRepository


class CheckLimitsUseCase:
    def __init__(self, subscription_repo: SubscriptionRepository) -> None:
        self._subscription_repo = subscription_repo

    async def execute(self, user_id: int) -> dict[str, int]:
        subscription = await self._subscription_repo.get_active_by_user(user_id)
        if not subscription:
            return {"channels_limit": 0, "is_active": False}
        return {
            "channels_limit": subscription.channels_limit,
            "is_active": True,
        }
