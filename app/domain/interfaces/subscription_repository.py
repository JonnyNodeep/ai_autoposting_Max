from abc import ABC, abstractmethod

from app.domain.entities.subscription import Subscription


class SubscriptionRepository(ABC):
    @abstractmethod
    async def get_active_by_user(self, user_id: int) -> Subscription | None: ...

    @abstractmethod
    async def get_by_id(self, subscription_id: int) -> Subscription | None: ...

    @abstractmethod
    async def create(self, subscription: Subscription) -> Subscription: ...

    @abstractmethod
    async def update(self, subscription: Subscription) -> Subscription: ...

    @abstractmethod
    async def deactivate(self, user_id: int) -> None: ...
