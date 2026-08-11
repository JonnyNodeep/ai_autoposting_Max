from loguru import logger

from app.application.auth.admin_access import ensure_admin_subscription, is_admin_max_user
from app.config import settings
from app.domain.entities.user import User
from app.domain.entities.subscription import Subscription
from app.domain.value_objects.subscription_tier import SubscriptionTier
from app.domain.value_objects.subscription_status import SubscriptionStatus
from app.domain.interfaces.user_repository import UserRepository
from app.domain.interfaces.subscription_repository import SubscriptionRepository
from app.domain.interfaces.max_client import MaxAPIClient


class BetaFullError(Exception):
    """Raised when beta user cap is reached for a new user."""


class RegisterUserUseCase:
    def __init__(
        self,
        user_repo: UserRepository,
        subscription_repo: SubscriptionRepository,
        max_client: MaxAPIClient,
        *,
        session=None,
    ) -> None:
        self._user_repo = user_repo
        self._subscription_repo = subscription_repo
        self._max_client = max_client
        self._session = session

    async def execute(self, max_user_id: int, username: str | None, first_name: str, last_name: str | None) -> User:
        existing = await self._user_repo.get_by_max_user_id(max_user_id)
        if existing:
            await self._user_repo.set_active(existing.id, True)
            if is_admin_max_user(max_user_id) and existing.id is not None:
                await ensure_admin_subscription(self._subscription_repo, existing.id)
            return existing

        if self._session is not None and not is_admin_max_user(max_user_id):
            from app.application.admin.beta_cap import BetaCapService

            beta = BetaCapService(self._session)
            if not await beta.can_register(max_user_id):
                await beta.add_to_waitlist(
                    max_user_id=max_user_id,
                    username=username,
                    first_name=first_name or "",
                    last_name=last_name,
                )
                raise BetaFullError("beta_full")

        user = await self._user_repo.create(
            User(
                max_user_id=max_user_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
            )
        )

        if is_admin_max_user(max_user_id) and user.id is not None:
            await ensure_admin_subscription(self._subscription_repo, user.id)
        else:
            await self._subscription_repo.create(
                Subscription(
                    user_id=user.id,
                    tier=SubscriptionTier.SOLO,
                    status=SubscriptionStatus.TRIAL,
                    channels_limit=SubscriptionTier.SOLO.channels_limit,
                    posts_per_day=1,
                    generations_quota=7,
                    generations_used=0,
                )
            )

        logger.info(f"New user registered: max_user_id={max_user_id} internal_id={user.id}")

        if settings.admin.max_user_id:
            await self._max_client.send_message_to_user(
                user_id=settings.admin.max_user_id,
                text=(
                    f"🆕 *Новый пользователь!*\n\n"
                    f"Имя: {first_name} {last_name or ''}\n"
                    f"max\\_user\\_id: `{max_user_id}`\n"
                    f"username: {username or 'нет'}"
                ),
                fmt="markdown",
            )

        return user
