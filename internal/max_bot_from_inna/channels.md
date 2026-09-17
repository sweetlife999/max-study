# Каналы общения

Все каналы входят в один pipeline:

```text
webhook → verify → normalize(InboundMessage) → durable enqueue → HTTP ACK
        → queue/core → OutboundMessage
        → outbox → ChannelPort.send
```

Конкретные адаптеры реализуют `ChannelPort`, а единственная таблица подключения
находится в `@inna/channels`. Ingress резолвит webhook alias из реестра, worker
получает через него downloader для voice/media, broadcast выбирает каналы по
`capabilities.broadcast`, outbox использует общую классификацию ошибок.

## Нативный agent progress

Worker запускает `sendChatAction("typing")` в начале inbound-хода и обновляет его
последовательно каждые 4 секунды до завершения обработки. Это нужно потому, что
Bot API держит typing не более 5 секунд; ошибки этого косметического контура не
влияют на ответ и не ретраят job.

`AgentRuntimeEvent` проецируется в безопасный общий `ChannelProgressEvent`:
наружу выходят только этапы `thinking/searching/schedule/ticket/working/composing`
(worker маппит имя tool на этап; аргументы/результаты tools и reasoning через
channel port не проходят) и `text-delta` для каналов с живым draft (widget).
Progress — best effort и не считается доставкой; канонический ответ после
postflight по-прежнему фиксирует transactional outbox.

Telegram и MAX работают в одной «progress-mode» схеме
(`createStatusMessageProgress` из `@inna/contracts`): на время цепочки tools
живёт **одно** тихое статус-сообщение с меткой этапа («Ищу в базе знаний…»,
«Проверяю расписание…»), которое канал правит не чаще ~1–2 раз/с и удаляет
на `flush`; финальный ответ приходит **новым** сообщением через outbox
(с уведомлением). Короткий ход без tools статуса не показывает: «Думаю…»
появляется только через ~1.2 с. `text-delta` мессенджеры игнорируют — черновик
ответа в чат не стримится. Telegram дополнительно держит `sendChatAction`
typing; у MAX индикатора нет, поэтому статус — единственный признак activity.
Redis-handoff плейсхолдера и Rich Message draft больше не используются.

Чанкинг (`splitForChannel`) общий: границы по абзацам вне пар `**…**` и
`[текст](url)`, открытый code fence закрывается и открывается заново, при >1
чанке каждый получает `(N/M)`; уведомление несёт только первый чанк ответа.
Повторяющиеся ошибки доставки одному чату outbox-диспетчер репортит по политике
«once»: первая за окно — `error`, дальше `debug` со счётчиком, успешная доставка
сбрасывает окно. Widget продолжает replayable SSE: status/delta приходят сразу,
outbox завершает ход `replace + done`.

## Функциональный паритет

| Возможность | Telegram | MAX | Widget |
|---|---:|---:|---:|
| Текст и Markdown | да | да | да |
| Reply на сообщение | да | да | привязка ответа к `turnId` |
| Consent/actions | нативные callback + ACK | нативные callback + ACK | интерактивные command-чипы |
| URL-кнопка | да | да | да |

Кнопки нативного Mastra `ask_user` используют единый callback
`ask:<ref12>:<n>` во всех каналах. Telegram и MAX отправляют его как native
callback payload, Widget получает эквивалентную команду `/ask <ref12> <n>`.
Полный option value и `toolCallId` остаются только в durable outbox и не уходят
клиенту; это сохраняет точный resume после рестарта и отклоняет старые кнопки.
| Голос → общий STT | да | да | да: MediaRecorder → S3 → STT |
| Image/file | явный fallback | явный fallback | нет загрузки |
| Broadcast | да | да | нет: получатель — ephemeral SSE-сессия |
| Locale | `language_code` интерфейса | `user_locale`, если поле пришло в update | slug сайта → `<html lang>` → язык браузера → RU |

Ограничения виджета отражают природу транспорта, а не отдельную бизнес-логику:
consent, safety, RAG, эскалация, память и outbox после нормализации общие.

## Markdown-контракт (общий знаменатель каналов)

`OutboundMessage.text` — markdown, и рендерят его четыре разных механизма:
Telegram — Rich Messages (`asTelegramRichMarkdown` передаёт выбранный агентом
Markdown без дополнительного выделения — Telegram парсит сам, кроме списков),
MAX — `format: "markdown"` (парсит платформа, кроме списков). Оба клиента
схлопывают одиночный `\n` между строками списка в пробел (CRO-46, изначально
найдено на MAX) и не документируют list syntax, поэтому оба адаптера гоняют
исходящий текст через общий `renderListsWithHardBreak` (`@inna/contracts`):
маркер `-`/`*` заменяется на plain-text `•`, перед каждой строкой списка
добавляется CommonMark hard break (два пробела), номера порядкового списка не
трогаются, fenced code исключён. Widget — собственный безопасный
рендер `apps/widget/src/app/markdown.tsx` (vnodes, без innerHTML) и админка —
`apps/admin/app/components/markdown-view.tsx` (React-узлы, тоже без innerHTML):
лента «Диалогов» показывает оператору те же сообщения, и она обязана выглядеть
ровно так же, как канал жителя, — иначе оператор судит об ответе по картинке,
которой житель не получал. Поэтому
допустимо только **общее подмножество**: абзацы, одиночные переносы
(hard break «два пробела + `\n`»), `**bold**`, `*italic*`, `` `code` ``,
блоки кода, списки, `[ссылки](url)` и backslash-эскейпы CommonMark
(`\*` → literal). Заголовки/цитаты/подчёркивание/таблицы — НЕ входят: часть
каналов покажет их сырым текстом.

Источник форматируемых админом текстов — единый компонент
`apps/admin/app/components/markdown-editor.tsx` (Tiptap + `@tiptap/markdown`,
`breaks: true`): его панель ограничена ровно этим подмножеством, а выход
запинен тестами на всех уровнях — от unit round-trip в админке до e2e
`apps/worker/src/markdown-delivery.e2e.test.ts` («markdown из админки доезжает
жителю дословно»). Добавляя форматирование, расширяй ОДНОВРЕМЕННО редактор,
все четыре рендера и эти тесты — иначе контракт разъедется.

Два наших собственных рендера (виджет и админка) — отдельные реализации, а не
общий пакет: виджету по `apps/widget/CLAUDE.md` запрещено импортировать
`@inna/*`, иначе ломается его независимая сборка и автодеплой. Готовую
библиотеку (`react-markdown` и подобные) не берём намеренно — она даёт полный
CommonMark, то есть отрендерила бы заголовки, цитаты и таблицы, которых житель
не увидел. Страховка от расхождения — общая фикстура
`docs/markdown-subset.fixtures.json`: её читают оба теста
(`apps/widget/src/app/markdown.test.tsx` и
`apps/admin/app/components/markdown-view.component.test.tsx`) и сверяют текст и
структуру тегов. Новый случай в подмножестве — новая запись в фикстуре, и она
обязана пройти в обоих.

## Треды и топики форумов

`InboundMessage.threadId` (Telegram `message_thread_id`) прокидывается ядром в
`OutboundMessage.threadId` во всех точках сборки исходящего, а Telegram-адаптер
передаёт его как `message_thread_id` в статус-сообщение, rich-финал и
чанки. Без этого параметра Bot API кладёт сообщение в
General-топик, даже если оно оформлено реплаем на сообщение из топика. Диалоги
разветвляются по топикам через `logicalConversationKey`; ответ оператора из
тикета восстанавливает топик из `conversation_key`
(`nativeThreadIdFromConversationKey`), потому что маршрут
`conversations.thread_id` (`channel:chatId`) его не хранит. MAX/Widget поле
игнорируют (топиков нет / свой threadId сессии живёт в транспорте виджета).

## Онбординг: `/start` и MAX `bot_started`

В Telegram первый контакт — текстовая команда `/start`. В MAX нажатие «Начать»
сообщения НЕ создаёт — приходит отдельный update `bot_started`; адаптер
(`packages/channels/max/src/index.ts`) синтезирует из него `InboundMessage` с
текстом `/start` (`messageId: bot_started:<timestamp>`, идемпотентность по
`max:start:<user>:<timestamp>`), поэтому ядро онбордит оба канала одним путём.

Во всех каналах первый обязательный этап — **согласие на обработку ПДн**.
Обычный текст, `/start`, `/language` и устаревшие onboarding-кнопки не могут
перескочить этот гейт; legacy-сессия без актуального consent возвращается на
`consent`.

Продолжение учитывает интерфейс канала: в Telegram порядок — **consent → язык →
знакомство → короткое обучение**; в MAX — **consent → язык**; в Widget язык
страницы уже авторитетен, поэтому после consent онбординг считается завершённым.
После принятия согласия backend сохраняет соответствующий этап во всех трёх
каналах, а Widget дополнительно не отправляет action-команды до локального
interstitial consent.

Приоритет языка различается осознанно. В Telegram/MAX явный `/language` закрепляет
выбор пользователя; иначе уверенный язык сообщения дополняет locale интерфейса.
В Widget язык сайта из `/ru`, `/en` или `/tt` авторитетен для UI, STT и ответа;
язык браузера используется только когда сайт не сообщил поддерживаемую локаль,
а если и он не дал поддерживаемого значения — используется RU.
Команда `/language` видна в нативных меню Telegram/MAX и в `/help`; до consent
она возвращает на общий consent-экран.

Widget показывает low-risk evidence-backed вывод нативного Mastra stream как
provisional `delta*`. После postflight транзакционный outbox атомарно добавляет
`replace + done`: клиент заменяет provisional текст принятым ответом либо безопасным
reject и сходится к тому же состоянию после reconnect. High-risk вывод до postflight
не публикуется. Каждый чанк содержит `turnId`, а SSE передаёт Redis Stream id в поле
`id:`. Браузер
автоматически посылает `Last-Event-ID` при reconnect, поэтому ingress повторяет
только непрочитанный хвост, а клиент отбрасывает чужие turn и повторные event id.
История ограничена по длине и TTL; чтение не удаляет события до успешной записи
в SSE.

## Подключение окружения

- Telegram: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`; webhook можно вести
  на `/tg` или `/telegram`.
- MAX: `MAX_BOT_TOKEN`, `MAX_WEBHOOK_SECRET`; webhook ведёт на `/max`, а secret
  должен передаваться MAX в `X-Max-Bot-Api-Secret`.
- Widget: `WIDGET_SESSION_SECRET`, `WIDGET_ALLOWED_ORIGINS`; транспорт использует
  `/widget/session`, `/widget`, `/widget/voice`, `/widget/stream` и
  `/widget/feedback`. Loader обязательно задаёт iframe policy `allow="microphone"`.

MAX rate limit соблюдается cluster-wide: до 25 rps глобально (ниже потолка 30)
и не чаще двух сообщений в секунду на диалог. Для `platform-api2.max.ru` адаптер
добавляет официальный Russian Trusted Root CA к стандартному Node trust store,
не отключая TLS-проверку. Callback подтверждается через `/answers` до обработки
очередью. В production после задания секретов остаётся создать или обновить
webhook subscription в кабинете/API соответствующего мессенджера.

HTTP ACK webhook-а возвращается только после успешного durable enqueue. Нативный
ACK callback выполняется параллельно с enqueue и ограничен 1,5 с, поэтому
медленный API канала не удерживает webhook-запрос бесконечно.

## Как добавить следующий канал

1. Добавить kind в `ChannelKind` и пакет адаптера в `packages/channels/<name>`.
2. Реализовать `ChannelPort`: aliases, capabilities, verify, normalize, send и
   при необходимости ACK, downloader, error classifier и close.
3. Зарегистрировать адаптер один раз в `packages/channels/registry/src/index.ts`.
4. Прогнать контрактные тесты: verify fail-closed, idempotency, private-dialog
   policy, locale, callbacks, reply/buttons, лимиты сообщений и ошибки доставки.

Приложения менять не требуется, пока новый канал укладывается в общий контракт.
