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
```

`seed` идемпотентен: второй запуск ничего не меняет. Сервисы `api`, `bot` и `web` добавляют
свои ветки — места для них размечены в [`compose.yaml`](compose.yaml).

## Repository

| Путь | Что там |
|---|---|
| `backend/` | пакет `campus`: конфиг, модели, миграции, доменные сервисы, клиент MAX, i18n, seed |
| `web/` | мини-приложение: Vite + React + TypeScript + `@maxhub/max-ui` |
| `config/` | YAML вуза (§6 контракта) |
| `docs/` | контракт и статус |
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
