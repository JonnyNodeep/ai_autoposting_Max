# AI Content Studio for MAX

## Требования

- Python 3.12+
- Poetry 1.8+
- Docker + Docker Compose (опционально)

## Локальный запуск (Poetry)

```bash
poetry env use 3.12
poetry install
poetry run pytest -q
poetry run uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
```

## Запуск тестов в Docker

```bash
docker compose run --rm app pytest -q
```

Если запуск идет на Python ниже 3.12, приложение и тесты завершатся сразу с понятной ошибкой о версии интерпретатора.

## Доступ к API

- Для всех `/api/*` (кроме webhook/health/metrics) обязателен заголовок `X-API-Token`.
- Для ресурсных операций также обязателен `owner_id` в query-параметрах.
- Для webhook MAX обязателен `APP_WEBHOOK_SECRET` и заголовок `X-Max-Bot-Api-Secret`.
