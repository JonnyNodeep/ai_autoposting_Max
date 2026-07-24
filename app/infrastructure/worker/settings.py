from arq.connections import RedisSettings

from app.infrastructure.worker.tasks import generate_post_task, generate_image_task
from app.config import settings


class WorkerSettings:
    functions = [generate_post_task, generate_image_task]
    redis_settings = RedisSettings.from_dsn(settings.redis.redis_url)
