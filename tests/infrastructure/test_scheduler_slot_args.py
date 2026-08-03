from unittest.mock import MagicMock

from app.infrastructure.scheduler.service import SchedulerService


def test_add_pipeline_job_passes_slot_time_in_args():
    svc = SchedulerService()
    svc._scheduler = MagicMock()
    svc.remove_pipeline_job = MagicMock()

    svc.add_pipeline_job(42, ["05:00", "12:30"])

    assert svc._scheduler.add_job.call_count == 2
    first = svc._scheduler.add_job.call_args_list[0]
    second = svc._scheduler.add_job.call_args_list[1]
    assert first.kwargs["args"] == [42, "05:00"]
    assert first.kwargs["hour"] == 5
    assert first.kwargs["minute"] == 0
    assert second.kwargs["args"] == [42, "12:30"]
    assert second.kwargs["hour"] == 12
    assert second.kwargs["minute"] == 30
