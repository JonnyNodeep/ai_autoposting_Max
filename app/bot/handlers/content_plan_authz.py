from collections.abc import Awaitable, Callable


OwnsChecker = Callable[[int], Awaitable[bool]]


async def is_authorized_content_callback(
    payload: str,
    owns_channel: OwnsChecker,
    owns_plan: OwnsChecker,
    owns_topic: OwnsChecker,
    owns_post: OwnsChecker,
) -> bool:
    try:
        if payload.startswith("channels:select:"):
            return await owns_channel(int(payload.split(":")[2]))
        if payload.startswith("plan:new:"):
            return await owns_channel(int(payload.split(":")[2]))
        if payload.startswith("plan:reprefs:"):
            return await owns_plan(int(payload.split(":")[2]))
        if payload.startswith("plan:approve:"):
            return await owns_plan(int(payload.split(":")[2]))
        if payload.startswith("plan:time:custom:"):
            return await owns_plan(int(payload.split(":")[3]))
        if payload.startswith("plan:edittime:custom:"):
            return await owns_plan(int(payload.split(":")[3]))
        if payload.startswith("plan:time:set:"):
            return await owns_plan(int(payload.split(":")[3]))
        if payload.startswith("plan:time:"):
            return await owns_plan(int(payload.split(":")[2]))
        if payload.startswith("plan:edittime:"):
            return await owns_plan(int(payload.split(":")[2]))
        if payload.startswith("plan:sedit:time:"):
            return await owns_plan(int(payload.split(":")[3]))
        if payload.startswith("plan:sedit:custom:"):
            return await owns_plan(int(payload.split(":")[3]))
        if payload.startswith("plan:sedit:"):
            return await owns_plan(int(payload.split(":")[2]))
        if payload.startswith("plan:settings_view:"):
            return await owns_plan(int(payload.split(":")[2]))
        if payload.startswith("plan:settings:etoggle:"):
            return await owns_plan(int(payload.split(":")[3]))
        if payload.startswith("plan:freq:set:"):
            return await owns_plan(int(payload.split(":")[3]))
        if payload.startswith("plan:freq:"):
            return await owns_plan(int(payload.split(":")[2]))
        if payload.startswith("plan:visual:"):
            return await owns_plan(int(payload.split(":")[2]))
        if payload.startswith("plan:regenerate:"):
            return await owns_plan(int(payload.split(":")[2]))
        if payload.startswith("plan:etoggle:"):
            return await owns_plan(int(payload.split(":")[2]))
        if payload.startswith("plan:edit:"):
            return await owns_plan(int(payload.split(":")[2]))
        if payload.startswith("plan:delete:confirm:"):
            return await owns_plan(int(payload.split(":")[3]))
        if payload.startswith("plan:delete:"):
            return await owns_plan(int(payload.split(":")[2]))
        if payload.startswith("topic:approve:"):
            return await owns_topic(int(payload.split(":")[2]))
        if payload.startswith("topic:delete:"):
            return await owns_topic(int(payload.split(":")[2]))
        if payload.startswith("topic:edit:"):
            return await owns_topic(int(payload.split(":")[2]))
        if payload.startswith("topic:add:"):
            return await owns_plan(int(payload.split(":")[2]))
        if payload.startswith("post:generate:"):
            return await owns_topic(int(payload.split(":")[2]))
        if payload.startswith("post:generate_all:"):
            return await owns_plan(int(payload.split(":")[2]))
        if payload.startswith("post:image:"):
            return await owns_post(int(payload.split(":")[2]))
        if payload.startswith("post:publish:"):
            return await owns_post(int(payload.split(":")[2]))
        if payload.startswith("edit:"):
            return await owns_post(int(payload.split(":")[2]))
        return True
    except (IndexError, ValueError):
        return False
