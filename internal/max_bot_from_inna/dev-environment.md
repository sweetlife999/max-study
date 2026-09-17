# Локальная dev-среда

Этот документ — операционный runbook для коллег и coding-агентов. Канонические
команды находятся в `Makefile`; здесь описано, какой профиль поднимать и что
именно должно перезагружаться. Секреты не нужно класть в Git или передавать
через `source .env`: команды загружают корневой `.env` через Node
`--env-file-if-exists`.

## Быстрый выбор команды

Перед первым запуском достаточно:

```bash
make dev
```

Команда создаёт `.env` из `.env.example`, устанавливает lockfile-зависимости,
поднимает базовую Docker-инфраструктуру, ждёт healthcheck-и, собирает пакет БД,
применяет миграции и запускает обычный app-стек с hot reload.
Для worker/агентских профилей заранее заполните `OPENROUTER_API_KEY` в `.env`;
`make dev-widget` в mock-режиме ключа не требует.

| Цель | Команда | Что запускается |
|---|---|---|
| Обычная разработка | `make dev` | ingress, inbound worker, broadcast, admin, widget |
| Выбранный набор | `make dev SERVICES=ingress,worker,widget` | только перечисленные компоненты |
| Только ingress | `make dev-ingress` | Hono API на `http://localhost:8080` |
| Только worker | `make dev-worker` | BullMQ-консюмеры, metrics на `:9465` |
| Webhook-каналы | `make dev-webhooks` | ingress + worker + outbox/broadcast |
| MAX (local) | `make dev SERVICES=max` | webhook-flow, endpoint `POST /max` |
| MAX (public callback) | `make dev-max` | cloudpub tunnel + webhook registration |
| Telegram | `make dev-telegram` | polling + worker + broadcast/outbox; нужен dev bot token |
| Widget UI | `make dev-widget` | Vite proxy innopolis.ru, mock transport, `:5173` |
| Widget end-to-end | `make dev-widget-agent` | widget + локальные ingress/worker/broadcast, реальный agent API |
| Полный RAG | `make dev-rag` | inbound + crawler + ingress + widget + Studio + ClickHouse |
| Replay RAG | `make dev-up SERVICES=rag-replay` | production-like RAG без crawler; не изменяет импортированный corpus snapshot |
| Админка | `make dev-admin` | Next.js HMR на `http://localhost:3001` |
| Studio | `make dev-studio` | Mastra Studio с hot reload на `http://localhost:4111` |
| Production-like Studio | `make studio` | сборка Mastra bundle и запуск без HMR |
| Агентский фон | `make dev-up SERVICES=...` | выбранные сервисы под управляемой process group |

`make stack` — алиас `make dev`.

### Параллельные worktree

Порты и Docker Compose project изолируются по worktree. Основной checkout
`inna` использует привычные базовые порты; worktree с суффиксом-номером
получает слот `+номер×100` (`work3` → `+300`). Worktree с произвольным именем
(`fix-login` — так их создают Orca и Claude Code) получает **наименьший
свободный слот** автоматически: `scripts/dev-port-slot.mjs` смотрит все
`git worktree list` этого репозитория, берёт первый незанятый `+N×100` и
закрепляет его в `.dev/port-offset` (гитигнорен) — слот стабилен на всё время
жизни worktree. `DEV_PORT_OFFSET=...` по-прежнему переопределяет всё. Команды
выполняются из самого worktree, поэтому `.dev/`, tmux и Compose volumes тоже
не смешиваются:

```bash
# основной checkout
make dev-tmux SERVICES=widget

# /path/to/inna-work3 — одновременно с основным checkout
make dev-tmux SERVICES=widget

# ~/orca/workspaces/inna/fix-login — слот выделяется сам на первом make
make dev-slot                       # показать закреплённый слот и все порты (JSON)
make dev-slot SET=700               # закрепить слот явно
DEV_PORT_OFFSET=500 make dev-tmux SERVICES=widget   # разовое переопределение
```

Для `work3` Vite будет слушать `http://localhost:5473`, ingress — `:8380`,
Postgres — `:5732`, а ClickHouse — `:8623`. Полный набор фактических портов
виден через `make dev-slot`, `make dev-status JSON=1` или `make dev-health JSON=1`.
Vite настроен с `strictPort=true`: занятый слот останавливает запуск с ошибкой,
поэтому health-check не может случайно проверить соседний worktree.

### Orca-worktree

[Orca](https://github.com/stablyai/orca) создаёт worktree в
`~/orca/workspaces/inna/<name>` (вне репозитория) и читает `orca.yaml` из
основного checkout:

- **setup** (`scripts/orca/setup.sh`) — после создания worktree: копирует
  гитигнореные env-файлы из `.worktreeinclude` (`.env`, `.env.local`,
  `.dokploy.env`) из основного checkout, `pnpm install --frozen-lockfile`,
  закрепляет port-слот. Идемпотентен, можно перезапустить руками. Инфру и
  watcher-ы не поднимает — это делает агент через `make dev-up SERVICES=...`.
- **archive** (`scripts/orca/archive.sh`) — перед архивированием/удалением:
  `make dev-stop` + `make reset` только для Compose project этого worktree
  (`inna-<name>`), основной `inna` и соседи не трогаются.
- **issueCommand** — стартовый промпт для worktree, созданного из Linear/GitHub
  тикета без явного промпта.

В Orca → Settings → Repository Hooks для `inna` источник команд должен быть
**«orca.yaml only»** (или «Run both»); «Local only» игнорирует файл. Политика
запуска setup — «Run by default»; агент стартует сразу (`start-immediately`),
setup идёт параллельно в отдельном терминале — перед первым `make dev-up`
дождитесь его окончания (или переключите репозиторий на «wait-for-setup»).

Проверка из любого worktree: `make dev-slot` (слот и порты), `make
print-compose-project` (имя Compose project), `orca worktree current --json`.

`make dev` принимает список через запятую, как `docker compose up service-a
service-b`:

```bash
make dev SERVICES=widget
make dev SERVICES=ingress,worker,widget
make dev SERVICES=telegram,widget
make dev SERVICES=webhooks,admin
```

### Агентский режим: up / status / health / logs / stop

Coding-агентам следует использовать managed lifecycle, чтобы hot-reload
процессы можно было диагностировать и гарантированно завершить:

```bash
make dev-up SERVICES=telegram,widget
make dev-status
make dev-health
make dev-logs LINES=200
make dev-logs FOLLOW=1
make dev-stop
```

`dev-up` запускает selector в фоне и записывает PID корневого процесса,
выбранные `SERVICES` и путь к логу в `.dev/dev.json`; вывод bootstrap и всех
watcher-ов идёт в `.dev/dev.log`. Все дочерние watcher-ы запускаются в той же
process group. `dev-stop` сначала отправляет группе `SIGTERM`, ждёт ограниченный
таймаут graceful shutdown и только затем делает адресный `SIGKILL` для этой же
группы. Лог ротируется после 10 MiB, состояние и логи игнорируются Git.

`make dev` остаётся foreground-командой для человека. Не запускайте агентские
стенды через `&`, `nohup`, `pkill`, `killall` или произвольные PID-файлы: это
ломает диагностику и может оставить orphan/zombie watcher-ы. После задачи агент
должен выполнить `make dev-stop`, если пользователь не попросил оставить стенд.
Каноническая инструкция для агентов — skill
[`dev-environment`](../.claude/skills/dev-environment/SKILL.md).

### tmux-viewer для интерактивной диагностики

Если нужно оставить рядом постоянный shell и поток логов, используйте tmux как
тонкий viewer поверх managed lifecycle:

```bash
make dev-tmux SERVICES=telegram,widget
make dev-tmux-status
make dev-tmux-attach
make dev-tmux-stop
```

Основной checkout получает сессию `inna-dev`; у worktree с числовым суффиксом
сессия по умолчанию `<slot>-dev` (`work3-dev`). Имя можно изменить:
`DEV_TMUX_SESSION=inna-widget make dev-tmux SERVICES=widget`.
`ATTACH=1` подключает текущий терминал сразу. tmux не является владельцем
watcher-ов, а `tmux kill-session` не гарантирует завершение viewer-процесса,
поэтому штатное завершение — `make dev-tmux-stop`, который сначала
останавливает process group и viewer-процесс.
Существующая немаркированная tmux-сессия никогда не переиспользуется.

Обычные компоненты (`ingress`, `worker`, `broadcast`, `admin`, `widget`)
запускаются одним dependency-aware `turbo watch`. Канальные профили `telegram`
и `webhooks` добавляют свой ingress-режим; если перечислить их вместе с
`widget` или `admin`, selector поднимет отдельную непересекающуюся watcher-группу.
`widget-agent`, `studio`, `rag` и `rag-replay` — самодостаточные профили и намеренно не
объединяются с другими режимами, чтобы не запустить два ingress на `:8080` или
два crawler/Studio процесса.

Для реплея production snapshot используйте `SERVICES=rag-replay`. Он поднимает
тот же inbound/ingress/broadcast/widget/Studio flow и ClickHouse, но не запускает
crawler-роль. Обычный `SERVICES=rag` при изменившемся каталоге источников вправе
заменить web-корпус, поэтому для неизменяемого baseline snapshot он не подходит.

## Что есть в репозитории

Сейчас в контракте каналов зарегистрированы только:

- `telegram` — polling для локального dev и webhook в ingress;
- `max` — webhook `POST /max`;
- `widget` — browser API, SSE и voice upload.

WhatsApp-адаптера в текущем коде нет: его нет в `ChannelKind`, в registry и в
workspace-пакетах. Поэтому `make dev-whatsapp` намеренно не существует и не
маскирует MAX под WhatsApp. Для добавления канала сначала нужен отдельный
`packages/channels/whatsapp` с `ChannelPort`, тестами и регистрацией в
`packages/channels/registry/src/index.ts`; после этого ему добавляется профиль
в `Makefile` и этот runbook. Рецепт границ монорепо — в skill
`extend-monorepo`.

## Профили и hot reload

### Backend и каналы

Составные профили используют `turbo watch` как единственного владельца
перезапуска backend-процессов:

- изменение `apps/*` или `packages/*` сначала проходит зависимый `build`;
- затронутый `tsx`-runtime перезапускается целиком;
- `SIGINT`/`SIGTERM` идут через graceful shutdown: BullMQ workers drain-ят
  активные jobs, закрываются Redis/PG/S3/HTTP/OTEL ресурсы;
- в `dev-rag` две роли одного worker запускаются отдельным supervisor-скриптом
  (`inbound` и `crawler`) и завершаются вместе, без осиротевших процессов.

Это намеренный process-level reload для Node-приложений: глобальные singleton-ы
и connection pools не накапливаются между версиями модуля. Не добавляйте второй
`tsx watch` внутрь составного `turbo watch`-профиля.

### Frontend

- widget использует штатный Vite HMR;
- admin использует Next.js HMR;
- Studio использует `mastra dev`, а изменения workspace-пакетов подхватывает
  внешний `turbo watch`.

У dev-proxy виджета есть `import.meta.hot.dispose`: при обновлении модуля он
отключает `MutationObserver`, снимает `window.message` listener, отменяет
таймеры/animation frames и удаляет старый iframe. Это обязательно для
долгоживущей вкладки: визуальное отсутствие дубля не означает отсутствие
удерживаемых ссылок.

Открывать widget proxy нужно по адресам:

```text
http://localhost:5173/en
http://localhost:5173/ru
http://localhost:5173/tt
http://localhost:5173/widget.html
```

В `make dev-widget` используется mock transport. Для сквозного запроса через
локальные ingress/worker/outbox используйте `make dev-widget-agent`; origin
виджета — `http://localhost:5173`, backend — `http://localhost:8080`.

## Docker-инфраструктура

Исходный код приложений запускается на хосте — так работают Vite/Next/tsx HMR.
Docker содержит только stateful-зависимости:

| Сервис | Порт | Базовый профиль | Назначение |
|---|---:|---|---|
| PostgreSQL 16 | 5432 + slot | всегда | реляционная БД и Mastra storage |
| Redis 7 | 6379 + slot | всегда | BullMQ, outbox, widget pub/sub |
| Qdrant 1.18.3 | 6333 + slot | всегда | dense/sparse RAG |
| MinIO (pinned release) | 9000 / 9001 + slot | всегда | S3 API / console |
| ClickHouse 26.3 | 8123 + slot | `rag` | Mastra traces, scores и Studio storage |

`make up` и все bootstrap-профили используют `docker compose up -d --wait`.
У сервисов есть healthcheck-и; ручные циклы с `sleep` для старта больше не нужны.
ClickHouse включается только `make dev-rag`, `make dev-studio` и `make studio`.

Полезные команды:

```bash
make ps       # состояние контейнеров и health
make logs     # логи всей локальной инфры, включая RAG-профиль
make down     # остановить контейнеры, данные сохранить
make reset    # остановить и удалить volumes; действие разрушительное
make obs-up   # необязательные Grafana/Tempo/Prometheus/Loki
```

Порты слота должны быть свободны. Не редактируйте `docker-compose.yml` для
каждого worktree: задайте `DEV_PORT_OFFSET`, а Makefile автоматически передаст
правильные host-порты, URL приложений и `COMPOSE_PROJECT_NAME`. Compose создаёт
раздельные volumes для каждого project name.

## Telegram

`make dev-telegram` — единственный рекомендуемый локальный Telegram flow:

1. проверяет `NODE_ENV`, `OPENROUTER_API_KEY` и `TELEGRAM_BOT_TOKEN`;
2. берёт token из переменной окружения или macOS Keychain
   (`service=inna.telegram-dev.bot-token`, `account=inna-dev`);
3. поднимает базовую infra и миграции;
4. удаляет активный webhook с `drop_pending_updates=false`;
5. запускает polling ingress, inbound worker и broadcast/outbox через
   `turbo watch`.

Используйте отдельного dev-бота. Polling не должен работать с production token:
он снимает production webhook. Изменения Telegram adapter, core, Mastra, LLM и
доставки подхватываются process-level reload-ом.

## MAX и обычные webhooks

MAX не имеет отдельного polling runtime: локальный flow — HTTP webhook в
ingress, затем общая очередь и outbox. Для agent-managed локального запуска:

```bash
make dev-up SERVICES=max
make dev-health
curl http://localhost:8080/health
make dev-stop
```

Путь webhook-а — `POST http://localhost:8080/max`; подпись проверяется через
`MAX_WEBHOOK_SECRET`, исходящая доставка требует `MAX_BOT_TOKEN`. Для реального
callback-а используйте `make dev-max`: команда поднимает cloudpub-туннель,
регистрирует `POST /max` в MAX Bot API и при остановке снимает webhook. Нужны
`MAX_BOT_TOKEN`, `MAX_WEBHOOK_SECRET`, `OPENROUTER_API_KEY` и установленный
`clo`; используйте отдельную dev-конфигурацию и не направляйте production
webhook на локальный ноутбук.

## Проверка запуска

После старта базового профиля:

```bash
curl -fsS http://localhost:8080/health
curl -fsS http://localhost:5173/widget.html
curl -fsS http://localhost:3001/api/health
```

Worker/broadcast не имеют пользовательского HTTP API; их readiness и metrics
доступны на `http://localhost:9465/health`, `:9465/metrics` и
`http://localhost:9474/health`, `:9474/metrics`. В RAG-профиле crawler использует
`:9466`.

Если процесс не стартует, проверяйте в таком порядке:

1. `node --version` (нужен Node `>=22.13`) и `pnpm --version`;
2. `make dev-status`, `make dev-health` и `make dev-logs LINES=200`;
3. `make ps` — все обязательные контейнеры должны быть `healthy`;
4. ключ `OPENROUTER_API_KEY` для worker/Telegram/RAG/Studio;
5. свободные порты и отсутствие второго dev-профиля;
6. `make dev-stop`, затем повторить нужный профиль; `make down` останавливает
   только Docker-инфру, а `make reset` используйте только
  когда допустимо удалить локальные данные.

Перед PR достаточно выполнить соответствующий smoke-профиль и затем обычные
проверки проекта: `make check`, `make typecheck`, `make test`; полный gate —
`make verify-release`.
