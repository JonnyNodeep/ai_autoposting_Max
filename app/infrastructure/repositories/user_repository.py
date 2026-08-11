from sqlalchemy import String, select, update, or_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.user import User
from app.domain.interfaces.user_repository import UserRepository
from app.infrastructure.models.user import UserModel


class SQLAlchemyUserRepository(UserRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: int) -> User | None:
        stmt = select(UserModel).where(UserModel.id == user_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_max_user_id(self, max_user_id: int) -> User | None:
        stmt = select(UserModel).where(UserModel.max_user_id == max_user_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def create(self, user: User) -> User:
        model = UserModel(
            max_user_id=user.max_user_id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            is_active=user.is_active,
            discount_percent=int(user.discount_percent or 0),
            referral_code=user.referral_code,
            referred_by_user_id=user.referred_by_user_id,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return self._to_entity(model)

    async def update(self, user: User) -> User:
        await self._session.execute(
            update(UserModel)
            .where(UserModel.id == user.id)
            .values(
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
                is_active=user.is_active,
                discount_percent=max(0, min(100, int(user.discount_percent or 0))),
                referral_code=user.referral_code,
                referred_by_user_id=user.referred_by_user_id,
            )
        )
        await self._session.flush()
        return user

    async def set_active(self, user_id: int, is_active: bool) -> None:
        await self._session.execute(
            update(UserModel).where(UserModel.id == user_id).values(is_active=is_active)
        )
        await self._session.flush()

    async def count_active(self, *, exclude_max_user_id: int | None = None) -> int:
        stmt = select(func.count()).select_from(UserModel).where(UserModel.is_active.is_(True))
        if exclude_max_user_id:
            stmt = stmt.where(UserModel.max_user_id != int(exclude_max_user_id))
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def search(
        self,
        q: str = "",
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[User], int]:
        raw = (q or "").strip()
        base = select(UserModel)
        count_stmt = select(func.count()).select_from(UserModel)
        if raw:
            like = f"%{raw}%"
            cond = or_(
                UserModel.username.ilike(like),
                UserModel.first_name.ilike(like),
                UserModel.last_name.ilike(like),
                UserModel.max_user_id.cast(String).ilike(like),
            )
            base = base.where(cond)
            count_stmt = count_stmt.where(cond)

        total = int((await self._session.execute(count_stmt)).scalar_one() or 0)
        stmt = base.order_by(UserModel.created_at.desc()).offset(offset).limit(limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._to_entity(m) for m in rows], total

    @staticmethod
    def _to_entity(model: UserModel) -> User:
        return User(
            id=model.id,
            max_user_id=model.max_user_id,
            username=model.username,
            first_name=model.first_name,
            last_name=model.last_name,
            discount_percent=int(getattr(model, "discount_percent", 0) or 0),
            referral_code=getattr(model, "referral_code", None),
            referred_by_user_id=getattr(model, "referred_by_user_id", None),
            created_at=model.created_at,
            is_active=model.is_active,
        )
