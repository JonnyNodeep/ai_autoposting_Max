from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.payment import Payment, PaymentStatus
from app.domain.interfaces.payment_repository import PaymentRepository
from app.infrastructure.models.payment import PaymentModel


class SQLAPaymentRepository(PaymentRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, payment_id: int) -> Payment | None:
        stmt = select(PaymentModel).where(PaymentModel.id == payment_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_yookassa_id(self, yookassa_id: str) -> Payment | None:
        stmt = select(PaymentModel).where(PaymentModel.yookassa_id == yookassa_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_user(self, user_id: int, limit: int = 20) -> list[Payment]:
        stmt = (
            select(PaymentModel)
            .where(PaymentModel.user_id == user_id)
            .order_by(PaymentModel.created_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [self._to_entity(m) for m in models]

    async def create(self, payment: Payment) -> Payment:
        model = PaymentModel(
            user_id=payment.user_id,
            yookassa_id=payment.yookassa_id,
            amount=payment.amount,
            tier=payment.tier,
            status=payment.status.value,
            confirmation_url=payment.confirmation_url,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return self._to_entity(model)

    async def update(self, payment: Payment) -> Payment:
        await self._session.execute(
            update(PaymentModel)
            .where(PaymentModel.id == payment.id)
            .values(
                status=payment.status.value,
                yookassa_id=payment.yookassa_id,
            )
        )
        await self._session.flush()
        return payment

    @staticmethod
    def _to_entity(model: PaymentModel) -> Payment:
        return Payment(
            id=model.id,
            user_id=model.user_id,
            yookassa_id=model.yookassa_id,
            amount=model.amount,
            tier=model.tier,
            status=PaymentStatus(model.status),
            confirmation_url=model.confirmation_url,
            created_at=model.created_at,
        )
