# AI Content Studio for MAX

SaaS-бэкенд для автогенерации и публикации контента в каналах мессенджера MAX.
UI пользователя — MAX-бот (отдельного веб-фронтенда нет).

## Требования

- Python 3.12+
- Poetry 1.8+
- Docker + Docker Compose (опционально)
- ffmpeg (для watermark видео; уже в Docker-образе)

## Локальный запуск (Poetry)

```bash
poetry env use 3.12
poetry install
cp .env.example .env   # заполните секреты
poetry run alembic upgrade head
poetry run pytest -q
poetry run uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
```

В отдельном процессе — ARQ worker:

```bash
poetry run arq app.infrastructure.worker.settings.WorkerSettings
```

## Запуск в Docker

```bash
docker compose up -d --build
docker compose run --rm app pytest -q
```

Postgres и Redis доступны только внутри compose-сети (порты на хост не публикуются).
Приложение: `http://localhost:8001`.

Если запуск идет на Python ниже 3.12, приложение и тесты завершатся сразу с понятной ошибкой о версии интерпретатора.

## Доступ к API

- Для всех `/api/*` (кроме webhook) обязателен заголовок `X-API-Token`.
- `/metrics` тоже требует `X-API-Token`.
- `/health` публичный (postgres + redis, без вызова MAX API) — для Docker healthcheck.
- Для ресурсных операций также обязателен `owner_id` в query-параметрах.
- Для webhook MAX обязателен `APP_WEBHOOK_SECRET` и заголовок `X-Max-Bot-Api-Secret`.
- VidGo callback: `POST /webhook/vidgo` (см. `VIDGO_CALLBACK_URL`, `VIDGO_WEBHOOK_TOKEN` в `.env.example`).

## VidGo

1. Укажите `VIDGO_API_KEY`.
2. Задайте публичный HTTPS URL: `VIDGO_CALLBACK_URL=https://your-domain.com/webhook/vidgo`.
3. Задайте `VIDGO_WEBHOOK_TOKEN` — он добавится как `?token=...` к callback.
4. Пока callback не дошёл, клиент ждёт результат через Redis и poll fallback.
