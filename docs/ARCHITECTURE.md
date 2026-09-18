# Архитектура: «Кампус» — онбординг, активности и QR-отметка в MAX

Этот документ — **контракт** для всех веток. Меняется только отдельным PR; реализация обязана ему соответствовать. Если реализация упирается в противоречие с документацией MAX — остановиться и зафиксировать в PR, а не импровизировать.

## 1. Продукт (MVP)
Первокурсник вуза/колледжа проходит онбординг-чек-лист и ходит на активности (студсовет, клубы, кураторские встречи). Посещение подтверждается сканом **ротирующего QR** на месте. Организатор создаёт события и показывает QR.

Роли: `student` (любой пользователь), `organizer` (по приглашению), `admin` (из env).
Не делаем: интеграции с ЛК/SSO вузов, геолокацию, рейтинги, LLM, расписание пар, webhook.

Продукт не привязан к конкретному вузу: всё вузовское — в YAML-конфиге (§6). Все данные в демо — синтетические.

## 2. Компоненты
```
             ┌──────────── MAX platform-api2.max.ru ────────────┐
             │  GET /updates (long polling)     POST/PUT /messages, /uploads, /answers
             └───────▲──────────────────────────────▲───────────┘
                     │                              │
 web (Caddy) ──/api──► api (FastAPI)          bot (1 реплика)
  React mini-app      │  проверка initData     ├─ polling → хендлеры
                      │  пишет в outbox        ├─ outbox worker (уведомления, напоминания)
                      │                        └─ ротация QR-картинок в чатах организаторов
                      └──────── Postgres 16 ───┘
```
- **Один Python-пакет `campus`**, два entrypoint: `python -m campus.api` и `python -m campus.bot`. Один Docker-образ.
- `api` **никогда не ходит в MAX API**. Всё исходящее — через таблицу `outbox`, которую разгребает `bot`.
- `bot` — строго одна реплика; при старте берёт `pg_advisory_lock(<const>)`, иначе завершается с ошибкой.
- Бизнес-логика только в `campus.domain` (сервисы). `api` и `bot` — тонкий транспорт, без SQL и без правил.

## 3. Структура репозитория
```
backend/
  pyproject.toml, uv.lock          # uv, Python 3.12
  alembic.ini, alembic/
  src/campus/
    config.py                      # pydantic-settings (env) + загрузка YAML вуза
    db/models.py, db/session.py
    domain/codes.py                # HMAC-коды отметки
    domain/services/*.py           # users, events, checkins, onboarding, organizers, outbox
    i18n/ru.yaml, i18n/en.yaml
    max/client.py                  # Protocol MaxClient
    max/http.py                    # реализация на httpx (+ CA Минцифры)
    max/fake.py                    # фейк для тестов
    max/types.py                   # pydantic-модели Update/Message/Attachment
    api/  (__main__.py, app.py, auth.py, routers/*, schemas.py)
    bot/  (__main__.py, dispatcher.py, handlers/*, workers/*, keyboards.py)
    seed.py                        # демо-данные: python -m campus.seed
  tests/unit, tests/integration    # pytest; интеграция — на реальном Postgres
  certs/russian_trusted_root_ca.pem
web/                               # Vite + React + TS + @maxhub/max-ui
config/university.example.yaml
deploy/Caddyfile, deploy/compose.prod.yaml, deploy/README.md
compose.yaml, .env.example, .dockerignore, README.md
.github/workflows/ci.yml
```

## 4. Данные (Postgres)
Все времена — `timestamptz` в UTC; часовой пояс вуза — из конфига, только для отображения. PK — `bigint identity`, кроме указанных.

| Таблица | Поля | Ограничения |
|---|---|---|
| `users` | id, max_user_id (bigint), first_name, lang (`ru`/`en`), consent_at null, created_at | unique(max_user_id) |
| `organizers` | user_id PK→users, invited_by null→users, created_at | |
| `organizer_invites` | token (text PK, ≥128 бит энтропии, url-safe), created_by null→users, expires_at, used_by null→users, used_at null | |
| `events` | id, title, description, kind, location, starts_at, ends_at, points (int ≥0), onboarding_step null (text), organizer_id→users, qr_seed (bytea 32), checkin_open (bool, default false), created_at | ends_at > starts_at; kind ∈ конфиг |
| `rsvps` | user_id, event_id, created_at | PK(user_id, event_id) |
| `checkins` | user_id, event_id, method (`qr`/`code`), created_at | PK(user_id, event_id) |
| `manual_step_completions` | user_id, step_key, created_at | PK(user_id, step_key) |
| `checkin_attempts` | id, user_id, created_at, success | индекс (user_id, created_at) — для rate limit |
| `outbox` | id, user_id→users, kind, payload jsonb, run_at, status (`pending`/`sent`/`failed`/`cancelled`), attempts, last_error null, dedup_key null, created_at | unique(dedup_key) where not null; индекс (status, run_at) |
| `qr_displays` | id, event_id, max_chat_id/user_id, message_id, active_until, last_rendered_window | |
| `kv` | key PK, value jsonb | маркер `updates_marker` для polling |

Прогресс онбординга **не хранится**, а вычисляется: шаг типа `event_kind` закрыт, если есть checkin на событие этого `kind` (или с `onboarding_step` = key); шаг `manual` — по `manual_step_completions`.

## 5. Коды отметки (контракт безопасности)
- `window = floor(unix_time / STEP)`, `STEP = CHECKIN_CODE_STEP_SECONDS` (default 10).
- `digest = HMAC-SHA256(qr_seed, b"campus-checkin:" + event_id(8 байт BE) + window(8 байт BE))`.
- `code` = 6 цифр по схеме dynamic truncation RFC 4226 от `digest`, с ведущими нулями.
- Принимаются окна `[window - CHECKIN_CODE_TOLERANCE_STEPS, window]` (default 2). Будущие окна — нет. Сравнение — `hmac.compare_digest`.
- `qr_seed` — `secrets.token_bytes(32)`, генерируется при создании события, **никогда не покидает backend** (ни в API-ответах, ни в логах).
- QR кодирует диплинк: `https://max.ru/{BOT_USERNAME}?startapp=ci_{event_id}_{code}` (сверить формат и лимит длины `startapp` с dev.max.ru/help/deeplinks). Под QR печатается `code` для ввода вручную.
- Отметка возможна, только если `checkin_open = true` и `now ∈ [starts_at - 30 мин, ends_at + 30 мин]`.
- Rate limit: не более 10 попыток за 10 минут на пользователя (по `checkin_attempts`) → `429`/сообщение «слишком много попыток».
- Ввод кода в чате без event_id: ищем среди событий с открытой отметкой; 0 совпадений — «неверный или устаревший код»; >1 — предложить выбрать событие кнопками.
- Повторная отметка — идемпотентна (200 с `already: true`), не ошибка.

## 6. Конфиг вуза (`config/university.example.yaml`)
```yaml
university: { name: {ru: "...", en: "..."}, timezone: "Europe/Moscow" }
languages: [ru, en]
event_kinds:
  - { key: council, title: {ru: "Студсовет", en: "Student council"}, default_points: 10 }
onboarding_steps:
  - { key: meet_curator, type: event_kind, event_kind: curator_meeting, title: {...}, description: {...} }
  - { key: join_group_chat, type: manual, title: {...}, description: {...} }
points_rewards:            # что дают баллы (текст, без логики выдачи)
  - { threshold: 50, title: {...} }
reminders_before: [PT24H, PT1H]
```
Валидируется Pydantic при старте обоих процессов; невалидный конфиг — процесс не стартует.

## 7. API мини-приложения
База `/api`. JSON. Аутентификация — заголовок `X-Max-Init-Data: <initData>` на каждом запросе; проверка подписи и срока (`auth_date` не старше `INIT_DATA_TTL_SECONDS`, default 86400) — **строго по dev.max.ru**. Пользователь создаётся при первом валидном запросе.
Ошибки: `{"error": {"code": "<snake_case>", "message": "<текст на языке пользователя>"}}` с HTTP-статусом (400/401/403/404/409/422/429).

**Коды ошибок (закрытый список, фронт на них завязан):** `invalid_init_data` (401), `consent_required` (403), `not_organizer` (403), `not_owner` (403), `checkin_closed` (403), `checkin_not_started` (403), `checkin_window_over` (403), `invalid_code` (400), `code_expired` (400), `ambiguous_code` (409), `event_not_found` (404), `step_not_found` (404), `step_not_manual` (409), `invite_invalid` (404), `invite_used` (409), `invite_expired` (409), `rate_limited` (429), `validation_error` (422). Новый код добавляется только вместе с правкой этого списка.

Каждый ответ содержит стандартный HTTP-заголовок `Date` (серверное время, UTC): по нему фронт корректирует расхождение часов клиента при ротации QR. `429` сопровождается `Retry-After` в секундах.
Пока нет `consent_at`, все эндпоинты, кроме `GET /me`, `POST /me/consent`, `PATCH /me`, отвечают `403 consent_required`.

| Метод | Путь | Кто | Тело → Ответ |
|---|---|---|---|
| GET | `/api/me` | все | → `{id, first_name, lang, consent: bool, is_organizer, is_admin, points, university: {name, timezone}}` |
| POST | `/api/me/consent` | все | → `Me` |
| PATCH | `/api/me` | все | `{lang}` → `Me` |
| GET | `/api/onboarding` | student | → `{steps: [{key, type, title, description, done, event_kind?}], done_count, total}` |
| POST | `/api/onboarding/{key}/complete` | student | только `manual` → `Step` |
| GET | `/api/events?scope=upcoming\|past` | student | → `{items: [Event]}`; `Event = {id, title, description, kind, kind_title, location, starts_at, ends_at, points, onboarding_step, checkin_open, rsvp: bool, checked_in: bool, attendees_count}` |
| GET | `/api/events/{id}` | student | → `Event` |
| PUT / DELETE | `/api/events/{id}/rsvp` | student | → `Event` (PUT ставит напоминания в outbox, DELETE отменяет) |
| POST | `/api/checkins` | student | `{event_id, code, method: "qr"\|"code"}` → `{event: Event, already: bool, points_total, completed_step?: Step}` |
| GET | `/api/org/events` | organizer | → `{items: [Event]}` своих |
| POST | `/api/org/events` | organizer | `{title, description, kind, location, starts_at, ends_at, points?, onboarding_step?}` → `Event` |
| PATCH | `/api/org/events/{id}` | organizer (владелец) | любые поля выше + `checkin_open` → `Event` |
| GET | `/api/org/events/{id}/qr` | владелец | → `{code, deeplink, window_started_at, expires_at, step_seconds}` (403, если `checkin_open=false`) |
| POST | `/api/org/events/{id}/qr/chat` | владелец | → 202; ставит в outbox показ ротирующего QR в чате с ботом |
| GET | `/api/org/events/{id}/attendance` | владелец | → `{items: [{user_id, first_name, method, checked_in_at}], rsvp_count, checkin_count}` |
| GET | `/api/org/events/{id}/attendance.csv` | владелец | CSV UTF-8 с BOM |
| POST | `/api/org/invites` | admin | → `{token, deeplink, expires_at}` |
| GET | `/api/config` | все | → `{event_kinds: [{key, title, default_points}], onboarding_steps: [{key, type, title, event_kind?}], languages: [..], university: {name, timezone}}` — из YAML вуза (§6). Единственный источник видов активностей и шагов для форм организатора; хардкодить их на фронте запрещено |
| GET | `/health` | — | → `{status: "ok", db: "ok"}` |

OpenAPI генерируется FastAPI и публикуется в `docs/openapi.json` (CI проверяет, что файл актуален) — фронт генерирует типы из него.

**Приватность кодов отметки:** код не должен попадать в адресную строку мини-приложения и в логи. `start_param` вида `ci_<event>_<code>` читается один раз при старте, отправляется в `POST /api/checkins` и не переносится в путь роутера; в логах `api`, `bot` и Caddy коды, `initData` и `qr_seed` маскируются.

## 8. Бот
- **Polling**: `GET /updates` с `marker` из `kv`, таймаут long-poll, маркер сохраняется **после** успешной обработки пачки. Ошибка хендлера одного апдейта логируется и не роняет цикл.
- `bot_started` / `/start` с payload:
  - пусто → приветствие, согласие (отдельное сообщение с кнопкой, §152-ФЗ ст. 9), выбор языка, главное меню;
  - `org_<token>` → принять приглашение организатора (одноразово, срок);
  - `ev_<id>` → карточка события.
  (Повторная доставка payload при открытом чате не гарантирована — ключевой сценарий отметки на `start`-payload **не опирается**.)
- Главное меню (inline): «Мой онбординг», «Ближайшие активности», «Ввести код», «Открыть приложение» (`open_app`). Для организатора — «Мои события» с кнопкой «Показать QR в чате».
- Карточка события: «Иду / Не иду», «Открыть в приложении».
- «Ввести код» → следующее текстовое сообщение из 6 цифр трактуется как код (§5).
- **QR в чате** (`qr_displays`): отправка PNG с QR + подпись (код, «отметились: N», до какого времени). Каждые `STEP` секунд — новая картинка через upload + `PUT /messages`. Длительность показа — до `ends_at` или кнопки «Стоп». Одна активная `qr_display` на организатора+событие.
- **Outbox worker**: берёт `pending` с `run_at <= now()` через `FOR UPDATE SKIP LOCKED`, отправляет, ретраи с экспоненциальной задержкой (макс. 5), затем `failed`. Типы: `reminder`, `checkin_confirmed`, `step_completed`, `invite_accepted`, `qr_display_start`.
- **Лимиты MAX**: глобальный токен-бакет ≤ 25 rps, ≤ 1 сообщение/с на чат; 429 → уважать задержку.
- Тексты сообщений ≤ 4000 символов; только из i18n-файлов, никакого текста в коде.

## 9. Mini-app (web)
Vite + React + TS + `@maxhub/max-ui`; MAX Bridge подключается строго по dev.max.ru/docs/webapps. Роутинг по `start_param`:
- `ci_<event>_<code>` → сразу `POST /api/checkins` (method `qr`) → экран результата;
- иначе → главный экран.

Экраны студента: согласие → главная (прогресс онбординга + ближайшие события) → событие → сканер (`openCodeReader` с запретом выбора файла; если метод недоступен — поле ввода кода).
Экраны организатора: мои события → создать/редактировать → **QR на весь экран** (обновляется по `expires_at`, крупный код под QR, счётчик отметившихся) → явка + CSV.
Все состояния: загрузка / пусто / ошибка с повтором. RU/EN. Работает в мобильной и веб-версии MAX.

## 10. Конфигурация (env)
`MAX_BOT_TOKEN`, `MAX_BOT_USERNAME`, `MAX_API_BASE_URL` (default `https://platform-api2.max.ru`), `DATABASE_URL`, `UNIVERSITY_CONFIG_PATH`, `ADMIN_MAX_USER_IDS` (через запятую), `PUBLIC_WEB_URL`, `CHECKIN_CODE_STEP_SECONDS`, `CHECKIN_CODE_TOLERANCE_STEPS`, `INIT_DATA_TTL_SECONDS`, `LOG_LEVEL`. Всё — в `.env.example` с комментариями. Секретов в репозитории нет.

## 11. Качество (обязательно для каждой ветки)
- `uv run ruff check`, `uv run ruff format --check`, `uv run pyright` (strict для `campus.domain`), `uv run pytest` — зелёные; покрытие `campus.domain` ≥ 90%, всего backend ≥ 80%.
- Web: `npm run lint`, `npm run typecheck`, `npm run test` (vitest), `npm run build`.
- TDD: сначала тест, потом код. Тесты с реальным Postgres (testcontainers или сервис CI), MAX — только через `max/fake.py`.
- Логи структурированные (JSON), без токена, `initData`, `qr_seed` и кодов.
- Conventional commits, маленькие логичные коммиты. Ветка → PR в `main` → CI зелёный → ревью → merge.
- `docker compose up --build` из чистого клона поднимает всё за ≤ 5 минут; миграции и seed — отдельные one-shot сервисы.
