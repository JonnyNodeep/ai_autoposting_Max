from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.infrastructure.services.vidgo_client import (
    VidGoClient,
    build_video_attempts,
    resolve_video_model,
)


def test_resolve_video_model_keeps_grok():
    assert resolve_video_model("grok-imagine") == "grok-imagine"
    assert resolve_video_model(None) == "seedance-1.5-pro"
    assert resolve_video_model("wan2.2-image-to-video-fast") == "wan2.2-image-to-video-fast"
    assert resolve_video_model("wan2.5-image-to-video") == "wan2.5-image-to-video"


def test_build_video_attempts_seedance_then_wan22_fast():
    attempts = build_video_attempts({"model": "seedance-1.5-pro"})
    assert [m for m, _ in attempts] == [
        "seedance-1.5-pro",
        "wan2.2-image-to-video-fast",
    ]
    assert attempts[0][1]["duration"] == 4
    assert attempts[0][1]["resolution"] == "720p"
    assert attempts[0][1]["generate_audio"] is False
    assert attempts[0][1]["aspect_ratio"] == "9:16"
    assert attempts[1][1] == {"resolution": "720p"}


def test_build_video_attempts_grok_then_wan22_fast():
    attempts = build_video_attempts(
        {"model": "grok-imagine", "duration": 6, "mode": "normal"}
    )
    assert attempts[0][0] == "grok-imagine"
    assert attempts[0][1]["duration"] == 6
    assert attempts[0][1]["mode"] == "normal"
    assert attempts[1][0] == "wan2.2-image-to-video-fast"
    assert attempts[1][1]["resolution"] == "720p"


def test_build_video_attempts_no_fallback_when_primary_is_wan22():
    attempts = build_video_attempts({"model": "wan2.2-image-to-video-fast"})
    assert len(attempts) == 1
    assert attempts[0][0] == "wan2.2-image-to-video-fast"
    assert attempts[0][1]["resolution"] == "720p"


@pytest.mark.asyncio
async def test_submit_video_seedance_payload_720p():
    with patch("app.infrastructure.services.vidgo_client.settings") as settings:
        settings.vidgo.api_key = "k"
        settings.vidgo.callback_url = "https://example.com/webhook/vidgo"
        settings.vidgo.webhook_token = "tok"

        client = VidGoClient(api_key="k")
        client._client = AsyncMock()
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {"data": {"task_id": "task-1"}}
        client._client.post = AsyncMock(return_value=response)

        with patch(
            "app.infrastructure.services.vidgo_tasks.register_task",
            new_callable=AsyncMock,
        ) as register_task:
            task_id = await client.submit_video(
                model="seedance-1.5-pro",
                prompt="move gently",
                image_url="https://img/x.png",
                duration=4,
                resolution="720p",
                aspect_ratio="9:16",
                fixed_lens=False,
                generate_audio=False,
                task_meta={"kind": "ai_test"},
            )

        assert task_id == "task-1"
        payload = client._client.post.await_args.kwargs["json"]
        assert payload["model"] == "seedance-1.5-pro"
        assert payload["callback_url"].startswith("https://example.com/webhook/vidgo")
        assert "token=tok" in payload["callback_url"]
        assert payload["input"]["duration"] == 4
        assert payload["input"]["resolution"] == "720p"
        assert payload["input"]["aspect_ratio"] == "9:16"
        assert payload["input"]["fixed_lens"] is False
        assert payload["input"]["generate_audio"] is False
        assert payload["input"]["image_urls"] == ["https://img/x.png"]
        register_task.assert_awaited_once()
        await client.close()


@pytest.mark.asyncio
async def test_submit_video_wan22_fast_payload():
    with patch("app.infrastructure.services.vidgo_client.settings") as settings:
        settings.vidgo.api_key = "k"
        settings.vidgo.callback_url = ""
        settings.vidgo.webhook_token = ""

        client = VidGoClient(api_key="k")
        client._client = AsyncMock()
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {"data": {"task_id": "task-w22"}}
        client._client.post = AsyncMock(return_value=response)

        task_id = await client.submit_video(
            model="wan2.2-image-to-video-fast",
            prompt="gentle motion",
            image_url="https://img/x.png",
            resolution="720p",
        )
        assert task_id == "task-w22"
        payload = client._client.post.await_args.kwargs["json"]
        assert payload["model"] == "wan2.2-image-to-video-fast"
        assert payload["input"]["resolution"] == "720p"
        assert "duration" not in payload["input"]
        assert "callback_url" not in payload
        await client.close()


@pytest.mark.asyncio
async def test_submit_video_omits_callback_when_unset():
    with patch("app.infrastructure.services.vidgo_client.settings") as settings:
        settings.vidgo.api_key = "k"
        settings.vidgo.callback_url = ""
        settings.vidgo.webhook_token = ""

        client = VidGoClient(api_key="k")
        client._client = AsyncMock()
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {"data": {"task_id": "task-2"}}
        client._client.post = AsyncMock(return_value=response)

        task_id = await client.submit_video(
            model="wan2.5-image-to-video",
            prompt="pan",
            image_url="https://img/x.png",
            resolution="720p",
        )
        assert task_id == "task-2"
        payload = client._client.post.await_args.kwargs["json"]
        assert "callback_url" not in payload
        assert payload["input"]["resolution"] == "720p"
        await client.close()


@pytest.mark.asyncio
async def test_generate_video_with_fallback_uses_wan22_after_primary_fails():
    client = VidGoClient(api_key="k")
    client.submit_video = AsyncMock(side_effect=["task-seedance", "task-wan22"])
    client.wait_for_task = AsyncMock(
        side_effect=[
            RuntimeError("seedance down"),
            {"status": "finished", "files": [{"file_url": "https://v.mp4"}]},
        ]
    )

    result = await client.generate_video_with_fallback(
        prompt="zoom",
        image_url="https://img/x.png",
        config={"model": "seedance-1.5-pro"},
        task_meta={"kind": "pipeline"},
    )
    assert result["status"] == "finished"
    assert client.submit_video.await_count == 2
    assert client.submit_video.await_args_list[0].kwargs["model"] == "seedance-1.5-pro"
    assert (
        client.submit_video.await_args_list[1].kwargs["model"]
        == "wan2.2-image-to-video-fast"
    )


@pytest.mark.asyncio
async def test_wait_for_task_uses_stored_result():
    client = VidGoClient(api_key="k")
    client.get_task_status = AsyncMock()
    with patch(
        "app.infrastructure.services.vidgo_tasks.get_stored_result",
        new_callable=AsyncMock,
        return_value={"status": "finished", "files": [{"file_url": "https://v.mp4"}]},
    ):
        result = await client.wait_for_task("t1", timeout=5)
    assert result["status"] == "finished"
    client.get_task_status.assert_not_called()


@pytest.mark.asyncio
async def test_wait_for_task_poll_fallback_and_timeout():
    client = VidGoClient(api_key="k")
    client.get_task_status = AsyncMock(return_value={"status": "processing", "progress": 10})
    with (
        patch(
            "app.infrastructure.services.vidgo_tasks.get_stored_result",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("app.infrastructure.services.vidgo_client.asyncio.sleep", new_callable=AsyncMock),
        pytest.raises(TimeoutError),
    ):
        await client.wait_for_task("t1", poll_interval=0, timeout=0)
