from arq.connections import RedisSettings

from app.infrastructure.worker.tasks import noop_task
from app.config import settings


class WorkerSettings:
    functions = [noop_task]
    redis_settings = RedisSettings.from_dsn(settings.redis.redis_url)
