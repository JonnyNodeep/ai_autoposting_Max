"""Tests for sync_channel_meta (public → private link refresh)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.application.channels.sync_channel_meta import sync_channel_meta
from app.domain.entities.channel import Channel
from app.domain.entities.pipeline_run import PipelineRun, PipelineStatus


def _channel(**kwargs) -> Channel:
    base = dict(
        id=10,
        owner_id=1,
        max_chat_id=999,
        title="Живые открытки",
        channel_link="https://max.ru/old_public",
    )
    base.update(kwargs)
    return Channel(**base)


@pytest.mark.asyncio
async def test_sync_updates_link_and_title():
    channel = _channel()
    max_client = AsyncMock()
    max_client.get_chat.return_value = {
        "title": "Живые открытки (закрытый)",
        "link": "https://max.ru/join/invite_abc",
    }
    ch_repo = AsyncMock()
    pipe_repo = AsyncMock()
    pipe_repo.get_active_by_channel.return_value = None

    result = await sync_channel_meta(channel, max_client, ch_repo, pipe_repo)

    assert result.channel_link == "https://max.ru/join/invite_abc"
    assert result.title == "Живые открытки (закрытый)"
    ch_repo.update.assert_awaited_once()
    max_client.get_chat.assert_awaited_once_with(999)


@pytest.mark.asyncio
async def test_sync_does_not_clobber_on_empty_link():
    channel = _channel(channel_link="https://max.ru/keep_me")
    max_client = AsyncMock()
    max_client.get_chat.return_value = {"title": "Живые открытки", "link": None}
    ch_repo = AsyncMock()
    pipe_repo = AsyncMock()
    pipe_repo.get_active_by_channel.return_value = None

    result = await sync_channel_meta(channel, max_client, ch_repo, pipe_repo)

    assert result.channel_link == "https://max.ru/keep_me"
    ch_repo.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_empty_string_link_no_clobber():
    channel = _channel(channel_link="https://max.ru/keep_me")
    max_client = AsyncMock()
    max_client.get_chat.return_value = {"title": "Живые открытки", "link": "  "}
    ch_repo = AsyncMock()

    result = await sync_channel_meta(channel, max_client, ch_repo, pipe_repo=None)

    assert result.channel_link == "https://max.ru/keep_me"
    ch_repo.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_updates_active_pipeline_run():
    channel = _channel()
    max_client = AsyncMock()
    max_client.get_chat.return_value = {
        "title": "Живые открытки",
        "link": "https://max.ru/join/new",
    }
    ch_repo = AsyncMock()
    active = PipelineRun(
        id=55,
        user_id=1,
        max_user_id=100,
        channel_id=10,
        channel_link="https://max.ru/old_public",
        status=PipelineStatus.ACTIVE,
    )
    pipe_repo = AsyncMock()
    pipe_repo.get_active_by_channel.return_value = active

    await sync_channel_meta(channel, max_client, ch_repo, pipe_repo)

    assert active.channel_link == "https://max.ru/join/new"
    pipe_repo.update.assert_awaited_once()
    assert pipe_repo.update.await_args.args[0].channel_link == "https://max.ru/join/new"


@pytest.mark.asyncio
async def test_sync_get_chat_failure_keeps_channel():
    channel = _channel()
    max_client = AsyncMock()
    max_client.get_chat.side_effect = RuntimeError("MAX down")
    ch_repo = AsyncMock()

    result = await sync_channel_meta(channel, max_client, ch_repo)

    assert result.channel_link == "https://max.ru/old_public"
    ch_repo.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_no_change_skips_channel_update_but_can_fix_run():
    channel = _channel(channel_link="https://max.ru/same")
    max_client = AsyncMock()
    max_client.get_chat.return_value = {
        "title": "Живые открытки",
        "link": "https://max.ru/same",
    }
    ch_repo = AsyncMock()
    active = MagicMock()
    active.id = 7
    active.channel_link = "https://max.ru/stale_on_run"
    pipe_repo = AsyncMock()
    pipe_repo.get_active_by_channel.return_value = active

    await sync_channel_meta(channel, max_client, ch_repo, pipe_repo)

    ch_repo.update.assert_not_awaited()
    assert active.channel_link == "https://max.ru/same"
    pipe_repo.update.assert_awaited_once()
