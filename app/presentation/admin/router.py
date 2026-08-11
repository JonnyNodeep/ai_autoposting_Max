"""HTML admin panel routes (/admin)."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger
from sqlalchemy import select

from app.application.admin.audit import write_audit
from app.application.admin.beta_cap import BetaCapService
from app.application.admin.broadcasts import SEGMENTS, create_broadcast
from app.application.admin.grant_generations import GrantGenerationsUseCase
from app.application.admin.settings_service import (
    KEY_AUDIO_WHITELIST,
    KEY_RSS_WHITELIST,
    KEY_VIDEO_WHITELIST,
    AppSettingsService,
)
from app.application.auth.feature_access import set_runtime_whitelists
from app.application.billing.pricing import set_runtime_prices
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.models.admin_audit_log import AdminAuditLogModel
from app.infrastructure.models.broadcast import BroadcastDeliveryModel, BroadcastModel
from app.infrastructure.models.channel import ChannelModel
from app.infrastructure.repositories.payment_repository import SQLAPaymentRepository
from app.infrastructure.repositories.subscription_repository import (
    SQLAlchemySubscriptionRepository,
)
from app.infrastructure.repositories.usage_stats_repository import UsageStatsRepository
from app.infrastructure.repositories.user_repository import SQLAlchemyUserRepository
from app.infrastructure.services.max_client import MaxAPIHTTPClient
from app.presentation.admin import auth as admin_auth
from app.config import settings

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

admin_web_router = APIRouter(prefix="/admin", tags=["Admin UI"])


def _flash(request: Request, message: str, kind: str = "ok") -> None:
    request.session["flash"] = {"message": message, "kind": kind}


def _pop_flash(request: Request) -> dict | None:
    return request.session.pop("flash", None)


@admin_web_router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "configured": admin_auth.admin_password_configured(),
            "error": None,
        },
    )


@admin_web_router.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, password: str = Form("")) -> HTMLResponse:
    if not admin_auth.admin_password_configured():
        return templates.TemplateResponse(
            request,
            "login.html",
            {"configured": False, "error": "ADMIN_WEB_PASSWORD не задан"},
            status_code=503,
        )
    if not admin_auth.verify_password(password):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"configured": True, "error": "Неверный пароль"},
            status_code=401,
        )
    admin_auth.login(request)
    return RedirectResponse("/admin/", status_code=303)


@admin_web_router.get("/logout")
async def logout(request: Request) -> RedirectResponse:
    admin_auth.logout(request)
    return RedirectResponse("/admin/login", status_code=303)


@admin_web_router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    async with async_session_factory() as session:
        stats = await UsageStatsRepository(session).get_stats()
        settings_svc = AppSettingsService(session)
        max_users = await settings_svc.get_max_users()
        users = SQLAlchemyUserRepository(session)
        admin_id = settings.admin.max_user_id or None
        used = await users.count_active(exclude_max_user_id=admin_id)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "stats": stats,
            "max_users": max_users,
            "beta_used": used,
            "flash": _pop_flash(request),
        },
    )


@admin_web_router.get("/users", response_class=HTMLResponse)
async def users_list(request: Request, q: str = "", page: int = 1) -> HTMLResponse:
    page = max(1, int(page or 1))
    per_page = 50
    async with async_session_factory() as session:
        repo = SQLAlchemyUserRepository(session)
        users, total = await repo.search(q, offset=(page - 1) * per_page, limit=per_page)
    pages = max(1, math.ceil(total / per_page))
    return templates.TemplateResponse(
        request,
        "users_list.html",
        {
            "users": users,
            "q": q,
            "page": page,
            "pages": pages,
            "total": total,
            "flash": _pop_flash(request),
        },
    )


@admin_web_router.get("/users/{user_id}", response_class=HTMLResponse)
async def user_detail(request: Request, user_id: int) -> HTMLResponse:
    async with async_session_factory() as session:
        users = SQLAlchemyUserRepository(session)
        subs = SQLAlchemySubscriptionRepository(session)
        payments = SQLAPaymentRepository(session)
        user = await users.get_by_id(user_id)
        if not user:
            _flash(request, "Пользователь не найден", "err")
            return RedirectResponse("/admin/users", status_code=303)
        sub = await subs.get_active_by_user(user_id)
        pays = await payments.get_by_user(user_id, limit=20)
        channels = list(
            (
                await session.execute(
                    select(ChannelModel)
                    .where(ChannelModel.owner_id == user_id)
                    .order_by(ChannelModel.id.desc())
                )
            ).scalars().all()
        )
        audits = list(
            (
                await session.execute(
                    select(AdminAuditLogModel)
                    .where(AdminAuditLogModel.user_id == user_id)
                    .order_by(AdminAuditLogModel.created_at.desc())
                    .limit(30)
                )
            ).scalars().all()
        )
    return templates.TemplateResponse(
        request,
        "user_detail.html",
        {
            "user": user,
            "sub": sub,
            "payments": pays,
            "channels": channels,
            "audits": audits,
            "flash": _pop_flash(request),
        },
    )


@admin_web_router.post("/users/{user_id}/discount")
async def user_set_discount(
    request: Request,
    user_id: int,
    discount_percent: int = Form(0),
) -> RedirectResponse:
    async with async_session_factory() as session:
        users = SQLAlchemyUserRepository(session)
        user = await users.get_by_id(user_id)
        if not user:
            _flash(request, "Пользователь не найден", "err")
            return RedirectResponse("/admin/users", status_code=303)
        pct = max(0, min(100, int(discount_percent)))
        before = user.discount_percent
        user.discount_percent = pct
        await users.update(user)
        await write_audit(
            session,
            actor="admin",
            action="set_discount",
            user_id=user_id,
            payload={"before": before, "after": pct},
        )
        await session.commit()
    _flash(request, f"Скидка установлена: {pct}%")
    return RedirectResponse(f"/admin/users/{user_id}", status_code=303)


@admin_web_router.post("/users/{user_id}/grant")
async def user_grant(
    request: Request,
    user_id: int,
    delta: int = Form(...),
    reason: str = Form("admin grant"),
) -> RedirectResponse:
    async with async_session_factory() as session:
        try:
            uc = GrantGenerationsUseCase(
                session, SQLAlchemySubscriptionRepository(session)
            )
            result = await uc.execute(user_id, int(delta), reason=reason, actor="admin")
            await session.commit()
            _flash(
                request,
                f"Квота сейчас {result['generations_used']}/{result['generations_quota']}",
            )
        except ValueError as exc:
            _flash(request, str(exc), "err")
    return RedirectResponse(f"/admin/users/{user_id}", status_code=303)


@admin_web_router.post("/users/{user_id}/extend")
async def user_extend(
    request: Request,
    user_id: int,
    days: int = Form(30),
) -> RedirectResponse:
    from datetime import UTC, datetime, timedelta

    from app.domain.value_objects.subscription_status import SubscriptionStatus

    days_i = max(1, min(365, int(days)))
    async with async_session_factory() as session:
        subs = SQLAlchemySubscriptionRepository(session)
        sub = await subs.get_active_by_user(user_id)
        if sub is None:
            _flash(request, "Нет активной подписки", "err")
            return RedirectResponse(f"/admin/users/{user_id}", status_code=303)
        now = datetime.now(UTC)
        base = sub.expires_at if sub.expires_at and sub.expires_at > now else now
        if base.tzinfo is None:
            base = base.replace(tzinfo=UTC)
        before = sub.expires_at
        sub.expires_at = base + timedelta(days=days_i)
        if sub.status == SubscriptionStatus.EXPIRED:
            sub.status = SubscriptionStatus.ACTIVE
        sub.expiry_notified_3d = False
        sub.expiry_notified_1d = False
        sub.expiry_notified_0d = False
        await subs.update(sub)
        await write_audit(
            session,
            actor="admin",
            action="extend_subscription",
            user_id=user_id,
            payload={
                "days": days_i,
                "before": before.isoformat() if before else None,
                "after": sub.expires_at.isoformat(),
            },
        )
        await session.commit()
    _flash(request, f"Продлено на {days_i} дн. → {sub.expires_at}")
    return RedirectResponse(f"/admin/users/{user_id}", status_code=303)


@admin_web_router.post("/users/{user_id}/plan")
async def user_set_plan(
    request: Request,
    user_id: int,
    tier: str = Form("solo"),
    posts_per_day: int = Form(1),
    reset_quota: str = Form(""),
) -> RedirectResponse:
    from app.application.billing.pricing import calc_quota, quote
    from app.domain.value_objects.subscription_status import SubscriptionStatus
    from app.domain.value_objects.subscription_tier import SubscriptionTier

    async with async_session_factory() as session:
        subs = SQLAlchemySubscriptionRepository(session)
        sub = await subs.get_active_by_user(user_id)
        if sub is None:
            _flash(request, "Нет активной подписки", "err")
            return RedirectResponse(f"/admin/users/{user_id}", status_code=303)
        try:
            q = quote(tier, int(posts_per_day))
            new_tier = SubscriptionTier(q.tier)
        except (ValueError, KeyError) as exc:
            _flash(request, str(exc), "err")
            return RedirectResponse(f"/admin/users/{user_id}", status_code=303)
        before = {
            "tier": sub.tier.value,
            "posts_per_day": sub.posts_per_day,
            "channels_limit": sub.channels_limit,
            "generations_quota": sub.generations_quota,
        }
        sub.tier = new_tier
        sub.posts_per_day = q.posts_per_day
        sub.channels_limit = q.channels
        if reset_quota:
            sub.generations_quota = calc_quota(q.posts_per_day)
            sub.generations_used = 0
        if sub.status == SubscriptionStatus.EXPIRED:
            sub.status = SubscriptionStatus.ACTIVE
        await subs.update(sub)
        await write_audit(
            session,
            actor="admin",
            action="set_plan",
            user_id=user_id,
            payload={
                "before": before,
                "after": {
                    "tier": sub.tier.value,
                    "posts_per_day": sub.posts_per_day,
                    "channels_limit": sub.channels_limit,
                    "generations_quota": sub.generations_quota,
                    "reset_quota": bool(reset_quota),
                },
            },
        )
        await session.commit()
    _flash(request, f"Тариф → {sub.tier.value} / {sub.posts_per_day} пуб./день")
    return RedirectResponse(f"/admin/users/{user_id}", status_code=303)


@admin_web_router.get("/beta", response_class=HTMLResponse)
async def beta_page(request: Request) -> HTMLResponse:
    async with async_session_factory() as session:
        settings_svc = AppSettingsService(session)
        max_users = await settings_svc.get_max_users()
        users = SQLAlchemyUserRepository(session)
        admin_id = settings.admin.max_user_id or None
        used = await users.count_active(exclude_max_user_id=admin_id)
        waitlist = await BetaCapService(session).list_pending(200)
    return templates.TemplateResponse(
        request,
        "beta.html",
        {
            "max_users": max_users,
            "used": used,
            "waitlist": waitlist,
            "flash": _pop_flash(request),
        },
    )


@admin_web_router.post("/beta/max-users")
async def beta_set_max(request: Request, max_users: int = Form(10)) -> RedirectResponse:
    async with async_session_factory() as session:
        await AppSettingsService(session).set_max_users(int(max_users))
        await write_audit(
            session,
            actor="admin",
            action="set_max_users",
            payload={"max_users": int(max_users)},
        )
        await session.commit()
    _flash(request, f"max_users = {max_users}")
    return RedirectResponse("/admin/beta", status_code=303)


@admin_web_router.post("/beta/admit/{entry_id}")
async def beta_admit(request: Request, entry_id: int) -> RedirectResponse:
    async with async_session_factory() as session:
        try:
            user = await BetaCapService(session).admit(entry_id, actor="admin")
            await session.commit()
            _flash(request, f"Впущен пользователь #{user.id}")
        except ValueError as exc:
            _flash(request, str(exc), "err")
    return RedirectResponse("/admin/beta", status_code=303)


@admin_web_router.post("/beta/admit-next")
async def beta_admit_next(request: Request, n: int = Form(5)) -> RedirectResponse:
    async with async_session_factory() as session:
        admitted = await BetaCapService(session).admit_next(int(n), actor="admin")
        await session.commit()
    _flash(request, f"Впущено пользователей: {len(admitted)}")
    return RedirectResponse("/admin/beta", status_code=303)


@admin_web_router.get("/payments", response_class=HTMLResponse)
async def payments_page(request: Request) -> HTMLResponse:
    async with async_session_factory() as session:
        payments = await SQLAPaymentRepository(session).list_recent(100)
        users = SQLAlchemyUserRepository(session)
        rows = []
        for p in payments:
            u = await users.get_by_id(p.user_id)
            rows.append({"payment": p, "user": u})
    return templates.TemplateResponse(
        request,
        "payments.html",
        {"rows": rows, "flash": _pop_flash(request)},
    )


@admin_web_router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request) -> HTMLResponse:
    async with async_session_factory() as session:
        svc = AppSettingsService(session)
        prices = await svc.get_billing_prices()
        rss = await svc.get_whitelist(KEY_RSS_WHITELIST)
        video = await svc.get_whitelist(KEY_VIDEO_WHITELIST)
        audio = await svc.get_whitelist(KEY_AUDIO_WHITELIST)
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "prices": prices,
            "rss": rss,
            "video": video,
            "audio": audio,
            "flash": _pop_flash(request),
        },
    )


@admin_web_router.post("/settings/prices")
async def settings_prices(request: Request) -> RedirectResponse:
    form = await request.form()
    prices: dict[str, Any] = {
        "base": {},
        "per_post": {},
        "channels": {},
        "posts_per_day_options": [],
    }
    for tier in ("solo", "creator", "studio"):
        prices["base"][tier] = int(form.get(f"base_{tier}") or 0)
        prices["per_post"][tier] = int(form.get(f"per_{tier}") or 0)
        prices["channels"][tier] = int(form.get(f"ch_{tier}") or 0)
    raw_opts = str(form.get("ppd_options") or "1,2,3,5")
    prices["posts_per_day_options"] = [
        int(x.strip()) for x in raw_opts.split(",") if x.strip().isdigit()
    ] or [1, 2, 3, 5]
    async with async_session_factory() as session:
        await AppSettingsService(session).set_billing_prices(prices)
        await write_audit(session, actor="admin", action="set_prices", payload=prices)
        await session.commit()
    set_runtime_prices(prices)
    _flash(request, "Цены сохранены")
    return RedirectResponse("/admin/settings", status_code=303)


@admin_web_router.post("/settings/features")
async def settings_features(
    request: Request,
    rss: str = Form(""),
    video: str = Form(""),
    audio: str = Form(""),
) -> RedirectResponse:
    async with async_session_factory() as session:
        svc = AppSettingsService(session)
        await svc.set_whitelist(KEY_RSS_WHITELIST, rss)
        await svc.set_whitelist(KEY_VIDEO_WHITELIST, video)
        await svc.set_whitelist(KEY_AUDIO_WHITELIST, audio)
        await write_audit(
            session,
            actor="admin",
            action="set_feature_whitelists",
            payload={"rss": rss, "video": video, "audio": audio},
        )
        await session.commit()
    set_runtime_whitelists(rss=rss, video=video, audio=audio)
    _flash(request, "Whitelist сохранён")
    return RedirectResponse("/admin/settings", status_code=303)


@admin_web_router.get("/broadcasts", response_class=HTMLResponse)
async def broadcasts_page(request: Request) -> HTMLResponse:
    async with async_session_factory() as session:
        broadcasts = list(
            (
                await session.execute(
                    select(BroadcastModel).order_by(BroadcastModel.id.desc()).limit(50)
                )
            ).scalars().all()
        )
    return templates.TemplateResponse(
        request,
        "broadcasts.html",
        {
            "broadcasts": broadcasts,
            "segments": SEGMENTS,
            "flash": _pop_flash(request),
        },
    )


@admin_web_router.post("/broadcasts")
async def broadcasts_create(
    request: Request,
    text: str = Form(""),
    segment: str = Form("all_active"),
    action: str = Form("create"),
) -> RedirectResponse:
    if action == "preview":
        admin_id = settings.admin.max_user_id
        if not admin_id:
            _flash(request, "ADMIN_MAX_USER_ID не задан", "err")
            return RedirectResponse("/admin/broadcasts", status_code=303)
        client = MaxAPIHTTPClient()
        try:
            await client.send_message_to_user(user_id=int(admin_id), text=text)
            _flash(request, "Превью отправлено админу")
        except Exception as exc:
            logger.exception("Broadcast preview failed")
            _flash(request, f"Превью не удалось: {exc}", "err")
        finally:
            await client.close()
        return RedirectResponse("/admin/broadcasts", status_code=303)

    async with async_session_factory() as session:
        try:
            bc = await create_broadcast(
                session, text=text, segment=segment, actor="admin"
            )
            await session.commit()
            _flash(request, f"Черновик #{bc.id} создан ({bc.total} получателей)")
            return RedirectResponse(f"/admin/broadcasts/{bc.id}", status_code=303)
        except ValueError as exc:
            _flash(request, str(exc), "err")
            return RedirectResponse("/admin/broadcasts", status_code=303)


@admin_web_router.get("/broadcasts/{broadcast_id}", response_class=HTMLResponse)
async def broadcast_detail(request: Request, broadcast_id: int) -> HTMLResponse:
    async with async_session_factory() as session:
        bc = await session.get(BroadcastModel, broadcast_id)
        if not bc:
            _flash(request, "Не найдено", "err")
            return RedirectResponse("/admin/broadcasts", status_code=303)
        deliveries = list(
            (
                await session.execute(
                    select(BroadcastDeliveryModel)
                    .where(BroadcastDeliveryModel.broadcast_id == broadcast_id)
                    .order_by(BroadcastDeliveryModel.id.asc())
                    .limit(500)
                )
            ).scalars().all()
        )
    return templates.TemplateResponse(
        request,
        "broadcast_detail.html",
        {"bc": bc, "deliveries": deliveries, "flash": _pop_flash(request)},
    )


@admin_web_router.post("/broadcasts/{broadcast_id}/send")
async def broadcast_send(request: Request, broadcast_id: int) -> RedirectResponse:
    try:
        from arq import create_pool
        from arq.connections import RedisSettings

        redis = await create_pool(RedisSettings.from_dsn(settings.redis.redis_url))
        await redis.enqueue_job("send_broadcast_job", broadcast_id)
        await redis.aclose()
        _flash(request, "Рассылка поставлена в очередь")
    except Exception as exc:
        logger.exception("Failed to enqueue broadcast")
        # Fallback: send inline
        from app.application.admin.broadcasts import send_broadcast_job

        result = await send_broadcast_job({}, broadcast_id)
        _flash(request, f"Отправлено сразу: {result}")
    return RedirectResponse(f"/admin/broadcasts/{broadcast_id}", status_code=303)


def mount_admin(app) -> None:
    """Attach static files for admin UI."""
    static_dir = BASE_DIR / "static"
    app.mount(
        "/admin/static",
        StaticFiles(directory=str(static_dir)),
        name="admin_static",
    )
