from arq.connections import RedisSettings

from app.infrastructure.worker.tasks import noop_task, send_broadcast_job
from app.config import settings


class WorkerSettings:
    functions = [noop_task, send_broadcast_job]
    redis_settings = RedisSettings.from_dsn(settings.redis.redis_url)
