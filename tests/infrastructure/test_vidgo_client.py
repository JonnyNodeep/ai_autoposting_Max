from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.infrastructure.services.vidgo_client import VidGoClient


@pytest.mark.asyncio
async def test_submit_video_includes_callback_when_configured():
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
                model="grok-imagine",
                prompt="move gently",
                image_url="https://img/x.png",
                task_meta={"kind": "ai_test"},
            )

        assert task_id == "task-1"
        payload = client._client.post.await_args.kwargs["json"]
        assert payload["callback_url"].startswith("https://example.com/webhook/vidgo")
        assert "token=tok" in payload["callback_url"]
        assert payload["input"]["duration"] == 6
        register_task.assert_awaited_once()
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
