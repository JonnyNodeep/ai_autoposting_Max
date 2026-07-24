import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.user import User
from app.infrastructure.repositories.user_repository import SQLAlchemyUserRepository


@pytest.mark.asyncio
async def test_create_user(user_repo: SQLAlchemyUserRepository, session: AsyncSession):
    user = await user_repo.create(
        User(max_user_id=123, first_name="Test", username="tester")
    )
    await session.commit()

    assert user.id is not None
    assert user.max_user_id == 123
    assert user.first_name == "Test"
    assert user.is_active is True


@pytest.mark.asyncio
async def test_get_by_max_user_id(user_repo: SQLAlchemyUserRepository, session: AsyncSession):
    user = await user_repo.create(
        User(max_user_id=456, first_name="FindMe", username="findme")
    )
    await session.commit()

    found = await user_repo.get_by_max_user_id(456)
    assert found is not None
    assert found.first_name == "FindMe"


@pytest.mark.asyncio
async def test_get_by_max_user_id_not_found(user_repo: SQLAlchemyUserRepository):
    found = await user_repo.get_by_max_user_id(999999)
    assert found is None
