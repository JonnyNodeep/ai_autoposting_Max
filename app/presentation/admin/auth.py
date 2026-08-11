"""Admin web auth helpers (cookie session)."""

from __future__ import annotations

from secrets import compare_digest

from fastapi import Request

from app.config import settings

SESSION_KEY = "admin_ok"


def admin_password_configured() -> bool:
    return bool((settings.admin.web_password or "").strip())


def session_secret() -> str:
    return (
        (settings.admin.session_secret or "").strip()
        or (settings.admin.web_password or "").strip()
        or (settings.admin.api_token or "").strip()
        or "dev-admin-secret"
    )


def verify_password(password: str) -> bool:
    expected = (settings.admin.web_password or "").strip()
    if not expected:
        return False
    return compare_digest(password or "", expected)


def is_logged_in(request: Request) -> bool:
    return bool(request.session.get(SESSION_KEY))


def login(request: Request) -> None:
    request.session[SESSION_KEY] = True


def logout(request: Request) -> None:
    request.session.clear()
