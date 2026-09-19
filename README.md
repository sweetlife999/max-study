# Кампус

Онбординг первокурсника, активности и отметка по ротирующему QR — мини-приложение и чат-бот
в мессенджере MAX.

**Контракт проекта — [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).** Он меняется отдельным PR;
реализация обязана ему соответствовать. Текущее состояние работ — [`docs/STATUS.md`](docs/STATUS.md).

Данные в демо синтетические, продукт не привязан к конкретному вузу: всё вузовское — в
[`config/university.example.yaml`](config/university.example.yaml).

## Быстрый старт

```sh
cp .env.example .env          # секретов в репозитории нет, .env в .gitignore
docker compose up --build --wait postgres
docker compose run --rm migrate
docker compose run --rm seed  # печатает приглашение организатора
docker compose up --build -d api bot
```

`seed` идемпотентен: второй запуск ничего не меняет. `api` слушает `http://localhost:8000`
(порт меняется через `API_PORT`), проверить — `curl http://localhost:8000/health`.

`bot` без `MAX_BOT_TOKEN` в `.env` не стартует и будет перезапускаться: токен выдаёт
@MasterBot, без него чат-сценарии §8 не проверить. `api` поднимется и без токена, но
`initData` мини-приложения проверить не сможет. Сервис `web` (Caddy) добавляет своя ветка —
место для него размечено в [`compose.yaml`](compose.yaml).

## Repository

| Путь | Что там |
|---|---|
| `backend/` | пакет `campus`: конфиг, модели, миграции, доменные сервисы, клиент MAX, i18n, seed, HTTP-API (`campus.api`) и бот (`campus.bot`) |
| `web/` | мини-приложение: Vite + React + TypeScript + `@maxhub/max-ui` |
| `config/` | YAML вуза (§6 контракта) |
| `docs/` | контракт, статус и сгенерированный `openapi.json` |
| `.github/workflows/` | `backend.yml` и `web.yml` — гейты качества §11 |

## Backend локально

Нужен [uv](https://docs.astral.sh/uv/) и Python 3.12.

```sh
cd backend
uv sync
uv run ruff check && uv run ruff format --check
uv run pyright
uv run pytest --cov=campus --cov-report=term-missing
```

Процессы запускаются теми же командами, что и в контейнере: `uv run python -m campus.api`
(FastAPI на порту 8000) и `uv run python -m campus.bot` (long polling, нужен `MAX_BOT_TOKEN`).

Контракт для фронта пересобирается из приложения; CI падает, если файл устарел:

```sh
cd backend
uv run python -m campus.api.openapi ../docs/openapi.json            # перегенерировать
uv run python -m campus.api.openapi ../docs/openapi.json --check    # проверить, как в CI
```

Интеграционные тесты работают на настоящем PostgreSQL: либо укажите `TEST_DATABASE_URL`, либо
оставьте его пустым — тогда testcontainers поднимет базу сам (нужен Docker). Живых вызовов MAX
тесты не делают: клиент подменяется на `campus.max.fake`.

Миграции:

```sh
cd backend
uv run alembic upgrade head        # DATABASE_URL из окружения
uv run alembic check               # модели не должны расходиться с миграциями
```

## Web локально

```sh
cd web
npm install
npm run lint && npm run typecheck && npm run test && npm run build
```

## Переменные окружения

Полный список — §10 контракта и комментарии в [`.env.example`](.env.example). Токен бота нужен
только процессам `bot` и `api` (проверка `initData`); ни один секрет в репозиторий не попадает.
