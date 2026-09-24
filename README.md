# AI Content Studio for MAX

SaaS-бэкенд для автогенерации и публикации контента в каналах мессенджера MAX.
UI пользователя — MAX-бот (отдельного веб-фронтенда нет).

Контентный путь: **AI Content Studio Blocks** (пайплайн из блоков на канал + cron).
Классический Content Plan из кода удалён (таблицы в БД deprecated).

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

## Медитационный канал (MAX + Telegram)

Один пайплайн на канал, три слота в день (UTC в БД → МСК в боте):

| Слот (МСК) | UTC   | Контент |
|------------|-------|---------|
| 07:30      | 04:30 | Аффirmации / материализация мыслей + женская озвучка Sunor |
| 12:11      | 09:11 | Дыхательная практика с инструкцией |
| 18:11      | 15:11 | Instrumental ambient: 2× Sunor → 4 трека → склейка ffmpeg (5 с crossfade) |

### Первичная настройка с нуля

1. Подключите MAX-канал в боте и при необходимости привяжите зеркало Telegram (кнопка в настройках канала).
2. Откройте **AI Content Studio** → выберите канал → **🧘 Preset «Медитационный канал»**.
3. Загрузите **3 референса картинок** (кнопка «🖼 Референсы картинок» — по одному на слот).
4. Заполните **очереди тем по слотам** («📚 Темы по слотам»): вручную или «Сгенерировать AI» с пожеланиями на слот.
5. Тестовый прогон одного слота (нужен активный `pipeline_runs.id`):

```bash
docker compose run --rm app python scripts/run_pipeline_once.py --run-id <ID> --slot-time 04:30
```

6. Запустите автопостинг (**▶ Запустить автопостинг**).

Если очередь слота пуста — публикация пропускается, владельцу приходит DM с просьбой добавить темы.

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
