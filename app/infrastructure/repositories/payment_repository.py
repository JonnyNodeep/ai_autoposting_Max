from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.payment import Payment, PaymentKind, PaymentStatus
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

    async def list_recent(self, limit: int = 100, offset: int = 0) -> list[Payment]:
        stmt = (
            select(PaymentModel)
            .order_by(PaymentModel.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def create(self, payment: Payment) -> Payment:
        before = int(payment.amount_before_discount or payment.amount or 0)
        model = PaymentModel(
            user_id=payment.user_id,
            yookassa_id=payment.yookassa_id,
            amount=payment.amount,
            amount_before_discount=before,
            discount_percent=int(payment.discount_percent or 0),
            tier=payment.tier,
            posts_per_day=payment.posts_per_day,
            kind=payment.kind.value if isinstance(payment.kind, PaymentKind) else str(payment.kind),
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
                posts_per_day=payment.posts_per_day,
                kind=payment.kind.value if isinstance(payment.kind, PaymentKind) else str(payment.kind),
                amount=payment.amount,
                amount_before_discount=int(payment.amount_before_discount or 0),
                discount_percent=int(payment.discount_percent or 0),
            )
        )
        await self._session.flush()
        return payment

    @staticmethod
    def _to_entity(model: PaymentModel) -> Payment:
        kind_raw = getattr(model, "kind", "new") or "new"
        try:
            kind = PaymentKind(kind_raw)
        except ValueError:
            kind = PaymentKind.NEW
        before = getattr(model, "amount_before_discount", None)
        if before is None or int(before) == 0:
            before = model.amount
        return Payment(
            id=model.id,
            user_id=model.user_id,
            yookassa_id=model.yookassa_id,
            amount=model.amount,
            amount_before_discount=int(before or 0),
            discount_percent=int(getattr(model, "discount_percent", 0) or 0),
            tier=model.tier,
            posts_per_day=getattr(model, "posts_per_day", 1) or 1,
            kind=kind,
            status=PaymentStatus(model.status),
            confirmation_url=model.confirmation_url,
            created_at=model.created_at,
        )
