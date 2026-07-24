from abc import ABC, abstractmethod

from app.domain.entities.payment import Payment


class PaymentRepository(ABC):
    @abstractmethod
    async def get_by_id(self, payment_id: int) -> Payment | None: ...

    @abstractmethod
    async def get_by_yookassa_id(self, yookassa_id: str) -> Payment | None: ...

    @abstractmethod
    async def get_by_user(self, user_id: int, limit: int = 20) -> list[Payment]: ...

    @abstractmethod
    async def create(self, payment: Payment) -> Payment: ...

    @abstractmethod
    async def update(self, payment: Payment) -> Payment: ...
