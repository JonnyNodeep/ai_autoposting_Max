"""Tests for OpenAI scene image retry in tale_video."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.application.pipeline.tale_video import (
    IMAGE_MAX_RETRIES,
    TaleGenerationError,
    _generate_scene_image_bytes,
)


def _ok_response(*, b64: bool = True) -> httpx.Response:
    if b64:
        body = {"data": [{"b64_json": base64.b64encode(b"png-bytes").decode()}]}
    else:
        body = {"data": [{"url": "https://cdn.example.com/img.png"}]}
    return httpx.Response(200, json=body)


def _error_response(status: int, body: str = '{"error":"fail"}') -> httpx.Response:
    return httpx.Response(status, text=body)


@pytest.mark.asyncio
async def test_scene_image_retries_on_503_then_succeeds():
    calls = {"n": 0}

    async def fake_post(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            return _error_response(503)
        return _ok_response()

    mock_client = AsyncMock()
    mock_client.post = fake_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("app.application.pipeline.tale_video.settings") as mock_settings,
        patch("app.application.pipeline.tale_video.asyncio.sleep", new_callable=AsyncMock),
        patch(
            "app.application.pipeline.tale_video.httpx.AsyncClient",
            return_value=mock_client,
        ),
    ):
        mock_settings.openai.api_key = "sk-test"
        mock_settings.openai.image_model = "gpt-image-2"
        mock_settings.openai.tale_image_size = "1536x1024"
        mock_settings.openai.tale_image_quality = "low"
        out = await _generate_scene_image_bytes("sleepy nursery scene")

    assert out == b"png-bytes"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_scene_image_no_retry_on_400():
    calls = {"n": 0}

    async def fake_post(*_args, **_kwargs):
        calls["n"] += 1
        return _error_response(400, '{"error":{"code":"content_policy_violation"}}')

    mock_client = AsyncMock()
    mock_client.post = fake_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("app.application.pipeline.tale_video.settings") as mock_settings,
        patch(
            "app.application.pipeline.tale_video.httpx.AsyncClient",
            return_value=mock_client,
        ),
    ):
        mock_settings.openai.api_key = "sk-test"
        mock_settings.openai.image_model = "gpt-image-2"
        mock_settings.openai.tale_image_size = "1536x1024"
        mock_settings.openai.tale_image_quality = "low"
        with pytest.raises(TaleGenerationError, match="400"):
            await _generate_scene_image_bytes("bad prompt")

    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_scene_image_exhausts_retries_on_503():
    calls = {"n": 0}

    async def fake_post(*_args, **_kwargs):
        calls["n"] += 1
        return _error_response(503)

    mock_client = AsyncMock()
    mock_client.post = fake_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("app.application.pipeline.tale_video.settings") as mock_settings,
        patch("app.application.pipeline.tale_video.asyncio.sleep", new_callable=AsyncMock),
        patch(
            "app.application.pipeline.tale_video.httpx.AsyncClient",
            return_value=mock_client,
        ),
    ):
        mock_settings.openai.api_key = "sk-test"
        mock_settings.openai.image_model = "gpt-image-2"
        mock_settings.openai.tale_image_size = "1536x1024"
        mock_settings.openai.tale_image_quality = "low"
        with pytest.raises(TaleGenerationError, match="503"):
            await _generate_scene_image_bytes("scene prompt")

    assert calls["n"] == IMAGE_MAX_RETRIES + 1
