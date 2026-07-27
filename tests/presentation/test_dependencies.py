import pytest
from fastapi import HTTPException

from app.config import settings
from app.presentation.api.dependencies import require_api_token


def test_require_api_token_rejects_when_not_configured():
    old_app = settings.app.api_token
    old_admin = settings.admin.api_token
    settings.app.api_token = ""
    settings.admin.api_token = ""
    try:
        with pytest.raises(HTTPException) as exc:
            require_api_token("any")
        assert exc.value.status_code == 503
    finally:
        settings.app.api_token = old_app
        settings.admin.api_token = old_admin


def test_require_api_token_accepts_app_token():
    old_app = settings.app.api_token
    old_admin = settings.admin.api_token
    settings.app.api_token = "test-app-token"
    settings.admin.api_token = ""
    try:
        require_api_token("test-app-token")
    finally:
        settings.app.api_token = old_app
        settings.admin.api_token = old_admin


def test_require_api_token_uses_admin_fallback():
    old_app = settings.app.api_token
    old_admin = settings.admin.api_token
    settings.app.api_token = ""
    settings.admin.api_token = "test-admin-token"
    try:
        require_api_token("test-admin-token")
    finally:
        settings.app.api_token = old_app
        settings.admin.api_token = old_admin


def test_require_api_token_rejects_invalid_token():
    old_app = settings.app.api_token
    old_admin = settings.admin.api_token
    settings.app.api_token = "test-app-token"
    settings.admin.api_token = ""
    try:
        with pytest.raises(HTTPException) as exc:
            require_api_token("wrong-token")
        assert exc.value.status_code == 403
    finally:
        settings.app.api_token = old_app
        settings.admin.api_token = old_admin
