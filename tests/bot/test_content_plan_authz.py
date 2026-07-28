import pytest

from app.bot.handlers.content_plan_authz import is_authorized_content_callback


async def _true(_id: int) -> bool:
    return True


async def _false(_id: int) -> bool:
    return False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "checker", "expected"),
    [
        ("channels:select:5", "channel", True),
        ("channels:select:5", "deny", False),
        ("plan:approve:9", "plan", True),
        ("plan:approve:9", "deny", False),
        ("topic:edit:3", "topic", True),
        ("post:publish:7", "post", True),
        ("edit:7", "post", True),
        ("unknown:thing", "deny", True),
        ("channels:select:abc", "channel", False),
    ],
)
async def test_is_authorized_content_callback(payload, checker, expected):
    owns = {
        "channel": _true if checker == "channel" else _false,
        "plan": _true if checker == "plan" else _false,
        "topic": _true if checker == "topic" else _false,
        "post": _true if checker == "post" else _false,
    }
    if checker == "deny":
        owns = {k: _false for k in owns}

    result = await is_authorized_content_callback(
        payload,
        owns_channel=owns["channel"],
        owns_plan=owns["plan"],
        owns_topic=owns["topic"],
        owns_post=owns["post"],
    )
    assert result is expected
