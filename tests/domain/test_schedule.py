from datetime import datetime, timedelta, UTC

from app.domain.entities.publish_schedule import PublishSchedule, ScheduleStatus


def test_schedule_entity_defaults():
    s = PublishSchedule(
        post_id=1,
        channel_id=10,
        scheduled_at=datetime.now(UTC) + timedelta(hours=2),
    )
    assert s.post_id == 1
    assert s.channel_id == 10
    assert s.status == ScheduleStatus.SCHEDULED
    assert s.sent_to_owner_at is None
    assert s.published_at is None


def test_schedule_status_transitions():
    s = PublishSchedule(post_id=1, channel_id=1, scheduled_at=datetime.now(UTC))
    s.status = ScheduleStatus.SENT_TO_OWNER
    assert s.status == ScheduleStatus.SENT_TO_OWNER
    s.status = ScheduleStatus.CONFIRMED
    assert s.status == ScheduleStatus.CONFIRMED
    s.status = ScheduleStatus.PUBLISHED
    assert s.status == ScheduleStatus.PUBLISHED
    s.status = ScheduleStatus.SKIPPED
    assert s.status == ScheduleStatus.SKIPPED
    s.status = ScheduleStatus.EXPIRED
    assert s.status == ScheduleStatus.EXPIRED


def test_schedule_status_values():
    assert ScheduleStatus.SCHEDULED == "scheduled"
    assert ScheduleStatus.SENT_TO_OWNER == "sent_to_owner"
    assert ScheduleStatus.CONFIRMED == "confirmed"
    assert ScheduleStatus.PUBLISHED == "published"
    assert ScheduleStatus.SKIPPED == "skipped"
    assert ScheduleStatus.EXPIRED == "expired"
