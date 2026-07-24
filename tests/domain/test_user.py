import pytest
from datetime import datetime

from app.domain.entities.user import User


def test_user_entity_creation():
    user = User(
        max_user_id=12345,
        first_name="Test",
        last_name="User",
        username="testuser",
    )
    assert user.max_user_id == 12345
    assert user.full_name == "Test User"
    assert user.is_active is True


def test_user_full_name_without_last_name():
    user = User(max_user_id=1, first_name="John")
    assert user.full_name == "John"


def test_user_full_name_with_last_name():
    user = User(max_user_id=2, first_name="John", last_name="Doe")
    assert user.full_name == "John Doe"
