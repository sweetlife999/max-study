# Стек (идея 05: онбординг + активности + QR-отметка)

Решения пользователя, 2026-09-17:
1. Фронт на React пишет Claude.
2. Два процесса: `api` и `bot`.
3. PostgreSQL.
4. QR обязателен в базовом сценарии (код в чате — только запасной путь).

## Базовые компоненты
- Python 3.12, `uv` + `uv.lock`; FastAPI; `maxapi` (MIT) или свой клиент на `httpx`; SQLAlchemy 2 async + Alembic; Pydantic (YAML-конфиг вуза).
- Фронт: React + TS + Vite + `@maxhub/max-ui`, `qrcode`.
- Postgres 16, Caddy, VPS в РФ. Корневой сертификат Минцифры — в образе.
- Качество: pytest, pytest-asyncio, ruff, pyright.

## Открыто
- Как жюри проверяет мини-приложение без HTTPS (спросить организаторов).
- Повторный `?start=` / `?startapp=`, `openCodeReader` в web.max.ru — живые тесты.
