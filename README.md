# Кампус

Онбординг первокурсника, активности и отметка по ротирующему QR — мини-приложение и чат-бот
в мессенджере MAX.

**Контракт проекта — [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).** Он меняется отдельным PR;
реализация обязана ему соответствовать. Текущее состояние работ — [`docs/STATUS.md`](docs/STATUS.md).

Данные в демо синтетические, продукт не привязан к конкретному вузу: всё вузовское — в
[`config/university.example.yaml`](config/university.example.yaml).

## Быстрый старт

```sh
cp .env.example .env  # секретов в репозитории нет, .env в .gitignore
# Впишите в .env настоящий MAX_BOT_TOKEN и имя бота в MAX_BOT_USERNAME.
docker compose up --build
```

Эта команда поднимает PostgreSQL, применяет миграции, идемпотентно загружает демо-данные,
запускает API, бота и Caddy с мини-приложением. Мини-приложение доступно на
`http://localhost:8080`; запросы к `/api` Caddy отправляет сервису `api` внутри Compose-сети.
Сам `api` также слушает `http://localhost:8000` (порт меняется через `API_PORT`), проверить его
можно командой `curl http://localhost:8000/health`. При первом запуске найдите в логах `seed`
приглашение организатора. Остановка стека: `docker compose down`.

`api` и `bot` без `MAX_BOT_TOKEN` в `.env` не стартуют и будут перезапускаться — это
намеренно: бот без токена не может опрашивать MAX, а api не может проверить подпись
`initData` ни одного запроса (§7). Токен выдаёт @MasterBot; впишите его в `.env` до запуска.

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
npm run api:types:check
npm run lint && npm run typecheck && npm run test && npm run build
```

После изменения `docs/openapi.json` обновите сгенерированные типы командой
`npm run api:types`.

Production-like образ мини-приложения собирается из каталога `web/` и запускается через Caddy:

```sh
docker build -t campus-web:local web
```

Для запуска внутри MAX одного публичного HTTPS недостаточно: владелец бота должен открыть
платформу MAX для партнёров → Чат-боты → бот → ⋮ → Настройки и в разделе «Мини-приложение»
сохранить адрес `https://max.fblrkus.ru` (или свой `PUBLIC_WEB_URL`) и выбрать кнопку запуска.
После этого проверьте кнопку «Открыть приложение» в чате и ссылку
`https://max.ru/t200_hakaton_max_bot?startapp`. Поле `web_app` у inline-кнопки содержит имя
бота, а не HTTPS-адрес сайта. [Инструкция MAX](https://dev.max.ru/docs/webapps/introduction).

## Переменные окружения

Полный список — §10 контракта и комментарии в [`.env.example`](.env.example). Токен бота нужен
только процессам `bot` и `api` (проверка `initData`); ни один секрет в репозиторий не попадает.
