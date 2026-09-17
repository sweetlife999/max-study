import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { rootCertificates } from "node:tls";
import { env } from "@inna/config";
import {
  answerSplitLimit,
  type ChannelDeliveryFailure,
  type ChannelPort,
  createStatusMessageProgress,
  type InboundMessage,
  MaxTransportUpdate,
  normalizeLocale,
  type OutboundMessage,
  renderListsWithHardBreak,
  resolveCallbackCommand,
  splitForChannel,
  type VerifyResult,
} from "@inna/contracts";
import { createResilient } from "@inna/resilience";
import { verifySecretTokenVersioned } from "@inna/security";
import { Agent, fetch as undiciFetch } from "undici";
import { z } from "zod";

/** Официальный production endpoint MAX Bot API. */
const MAX_API_BASE_URL = "https://platform-api2.max.ru";
/**
 * MAX Bot API принимает `POST /chats/{id}/actions` с `typing_on` (200 OK), но
 * платформа не показывает индикатор боту в приватном чате — проверено вживую.
 * Поэтому activity хода — только статус-сообщение (`publishProgress`): тихое,
 * правится по этапам, удаляется на flush; финал приходит новым сообщением.
 * Документированный MAX ceiling — два edits/s; оставляем небольшой запас.
 */
const PROGRESS_EDIT_INTERVAL_MS = 550;
const MAX_MESSAGE_LENGTH = 4000;
const MAX_FILE_BYTES = 25 * 1024 * 1024;
const MAX_USER_ID_RE = /^[1-9]\d*$/u;
const SAFE_EXTENSION_RE = /^[a-z0-9]{1,8}$/u;
const FENCE_LINE_RE = /^\s*(`{3,}|~{3,})/u;
/** CommonMark thematic break (`---`/`***`/`___`) — не в списке поддерживаемых
 * MAX элементов разметки (dev.max.ru/docs-api: жирный/курсив/зачёркнутый/
 * подчёркнутый/код/ссылки/упоминания/заголовки/цитаты — без списков и без
 * горизонтальных разделителей). Строка рендерится буквально, а не линией. */
const THEMATIC_BREAK_LINE_RE = /^\s*([-*_])\1{2,}\s*$/u;
/** Лейбл кнопки «новый диалог» на всех трёх языках — see `inlineKeyboard()`. */
const NEW_DIALOG_BUTTON_LABEL: Record<"en" | "ru" | "tt", string> = {
  en: "New dialogue",
  ru: "Начать новый диалог",
  tt: "Яңа диалог башлау",
};
/** chatId → mid последнего сообщения бота с клавиатурой в этом чате — see `send()`. */
const lastKeyboardMessageId = new Map<string, string>();
/** Только для тестов — модульный `Map` иначе утекает состояние между кейсами. */
export function resetMaxKeyboardTrackingForTests(): void {
  lastKeyboardMessageId.clear();
}
const MAX_BOT_COMMANDS = [
  { description: "Начать / Start / Башларга", name: "start" },
  { description: "Помощь / Help / Ярдәм", name: "help" },
  { description: "Язык / Language / Тел", name: "language" },
  {
    description: "Очистить контекст / Clear context / Контекстны чистартырга",
    name: "clear",
  },
  {
    description: "Новый диалог / New dialogue / Яңа диалог",
    name: "new",
  },
  {
    description: "Расписания / Schedules / Расписаниеләр",
    name: "schedule_list",
  },
  {
    description: "Отписаться / Unsubscribe / Язылудан баш тартырга",
    name: "stop",
  },
] as const;

const MaxUser = z.looseObject({
  first_name: z.string().nullable().optional(),
  is_bot: z.boolean().optional(),
  last_name: z.string().nullable().optional(),
  /** Deprecated MAX field retained for payloads from older API versions. */
  name: z.string().nullable().optional(),
  user_id: z.number(),
  username: z.string().nullable().optional(),
});
const MaxUserWithPhoto = MaxUser.extend({
  avatar_url: z.string().nullable().optional(),
  description: z.string().nullable().optional(),
  full_avatar_url: z.string().nullable().optional(),
});
const MaxChat = z.looseObject({
  dialog_with_user: MaxUserWithPhoto.nullable().optional(),
});
type ParsedMaxUpdate = z.infer<typeof MaxTransportUpdate>;
type ParsedMaxMessageCreatedUpdate = Extract<
  ParsedMaxUpdate,
  { update_type: "message_created" }
>;
type ParsedMaxCallbackUpdate = Extract<
  ParsedMaxUpdate,
  { update_type: "message_callback" }
>;
type ParsedMaxBotStartedUpdate = Extract<
  ParsedMaxUpdate,
  { update_type: "bot_started" }
>;
type ParsedMaxMessage = ParsedMaxMessageCreatedUpdate["message"];
type ParsedMaxAttachment = NonNullable<
  ParsedMaxMessage["body"]["attachments"]
>[number];

interface MaxRawAttachment {
  attachmentId: string;
  attachmentType: string;
  attachmentUrl?: string;
}

let maxDispatcher: Agent | undefined;
const maxUserProfileResilient = createResilient({
  name: "max-user-profile",
  retries: 1,
  timeoutMs: 8000,
});

/**
 * MAX с июля 2026 использует Russian Trusted Root CA, отсутствующий в Node CA store.
 * Добавляем закреплённый официальный root к стандартному набору, не отключая TLS verify.
 */
async function getMaxDispatcher(): Promise<Agent> {
  if (!maxDispatcher) {
    const russianRoot = await readFile(
      new URL("../certs/russian_trusted_root_ca.pem", import.meta.url),
      "utf8"
    );
    maxDispatcher = new Agent({
      connect: { ca: [...rootCertificates, russianRoot] },
    });
  }
  return maxDispatcher;
}

export async function closeMaxAgent(): Promise<void> {
  const dispatcher = maxDispatcher;
  maxDispatcher = undefined;
  maxStatusProgress.close();
  await dispatcher?.close();
}

/** Ошибка с bounded metadata: response body MAX намеренно не сохраняем и не логируем. */
export class MaxApiError extends Error {
  readonly retryAfterMs?: number;
  readonly status: number;

  constructor(status: number, retryDelayMs?: number) {
    super(`MAX Bot API → ${status}`);
    this.name = "MaxApiError";
    this.status = status;
    this.retryAfterMs = retryDelayMs;
  }
}

// Маппинг callback → команда — общий для всех каналов (`@inna/contracts`).
const callbackCommand = resolveCallbackCommand;

function primaryAttachment(
  message: ParsedMaxMessage
): ParsedMaxAttachment | undefined {
  const attachments = message.body.attachments ?? [];
  return (
    attachments.find((item) => item.type === "audio") ??
    attachments.find((item) => item.type === "image") ??
    attachments.find((item) => item.type === "file" || item.type === "video")
  );
}

function inboundKind(message: ParsedMaxMessage): InboundMessage["kind"] | null {
  const attachment = primaryAttachment(message);
  if (attachment?.type === "audio") {
    return "voice";
  }
  if (attachment?.type === "image") {
    return "image";
  }
  if (attachment?.type === "file" || attachment?.type === "video") {
    return "file";
  }
  return message.body.text !== null && message.body.text !== undefined
    ? "text"
    : null;
}

function maxRawAttachment(
  message: ParsedMaxMessage
): MaxRawAttachment | undefined {
  const attachment = primaryAttachment(message);
  if (!attachment) {
    return;
  }
  return {
    attachmentId: message.body.mid,
    attachmentType: attachment.type,
    ...(attachment.payload?.url
      ? { attachmentUrl: attachment.payload.url }
      : {}),
  };
}

function normalizeCallbackUpdate(
  update: ParsedMaxCallbackUpdate
): InboundMessage | null {
  const command = callbackCommand(update.callback.payload);
  const { message } = update;
  if (
    !(command && message) ||
    update.callback.user.is_bot ||
    message.recipient.chat_type !== "dialog"
  ) {
    return null;
  }
  return {
    channel: "max",
    channelUserId: String(update.callback.user.user_id),
    chatId: String(update.callback.user.user_id),
    idempotencyKey: `max:callback:${update.callback.callback_id}`,
    interactionId: update.callback.callback_id,
    kind: "text",
    locale: normalizeLocale(update.user_locale),
    messageId: message.body.mid,
    raw: { callbackData: update.callback.payload, chatType: "dialog" },
    receivedAt: Date.now(),
    senderFirstName:
      update.callback.user.first_name ?? update.callback.user.name ?? undefined,
    senderLastName: update.callback.user.last_name ?? null,
    senderUsername: update.callback.user.username ?? null,
    text: command,
  };
}

function normalizeBotStartedUpdate(
  update: ParsedMaxBotStartedUpdate
): InboundMessage | null {
  if (update.user.is_bot) {
    return null;
  }
  // Сообщения у события нет — синтезируем /start, чтобы пойти по тому же
  // пути приветствия, что и текстовая команда (ядро канал не различает).
  return {
    channel: "max",
    channelUserId: String(update.user.user_id),
    chatId: String(update.user.user_id),
    idempotencyKey: `max:start:${update.user.user_id}:${update.timestamp}`,
    kind: "text",
    locale: normalizeLocale(update.user_locale),
    messageId: `bot_started:${update.timestamp}`,
    raw: { chatType: "dialog" },
    receivedAt: Date.now(),
    senderFirstName: update.user.first_name ?? update.user.name ?? undefined,
    senderLastName: update.user.last_name ?? null,
    senderUsername: update.user.username ?? null,
    text: "/start",
  };
}

function normalizeMessageUpdate(
  update: ParsedMaxMessageCreatedUpdate
): InboundMessage | null {
  const { message } = update;
  const { sender } = message;
  if (!sender || sender.is_bot || message.recipient.chat_type !== "dialog") {
    return null;
  }
  const kind = inboundKind(message);
  if (!kind) {
    return null;
  }
  const rawAttachment = maxRawAttachment(message);
  return {
    channel: "max",
    channelUserId: String(sender.user_id),
    chatId: String(sender.user_id),
    idempotencyKey: `max:${message.body.mid}`,
    kind,
    locale: normalizeLocale(update.user_locale),
    messageId: message.body.mid,
    raw: rawAttachment
      ? { ...rawAttachment, chatType: "dialog" }
      : { chatType: "dialog" },
    receivedAt: Date.now(),
    replyToMessageId:
      message.link?.type === "reply" ? message.link.message?.mid : undefined,
    senderFirstName: sender.first_name ?? sender.name ?? undefined,
    senderLastName: sender.last_name ?? null,
    senderUsername: sender.username ?? null,
    text: message.body.text ?? undefined,
  };
}

/**
 * Режет исходящий текст по абзацам: продуктовый лимит ТЗ п.13.4
 * (ANSWER_SPLIT_MAX_CHARS, дефолт 1500) в пределах транспортного лимита MAX 4000.
 * Markdown-aware: пары `**`/`[ссылка](url)` не рвутся, code fence закрывается и
 * открывается заново, при >1 чанке каждый получает индикатор `(N/M)`.
 */
export function splitForMax(text: string): string[] {
  return splitForChannel(
    text,
    answerSplitLimit(MAX_MESSAGE_LENGTH, env.ANSWER_SPLIT_MAX_CHARS),
    { indicator: true }
  );
}

/**
 * MAX принимает `format: markdown`, но его документированное подмножество не
 * содержит списков и клиент схлопывает одиночные переводы строк в пробелы.
 * Общая реализация — `renderListsWithHardBreak` (`@inna/contracts`), Telegram
 * Rich Messages ловят идентичный баг и используют ту же функцию.
 */
function stripThematicBreaks(text: string): string {
  const output: string[] = [];
  let inFencedCode = false;
  for (const rawLine of text.replace(/\r\n?/gu, "\n").split("\n")) {
    if (FENCE_LINE_RE.test(rawLine)) {
      inFencedCode = !inFencedCode;
    } else if (!inFencedCode && THEMATIC_BREAK_LINE_RE.test(rawLine)) {
      continue;
    }
    output.push(rawLine);
  }
  return output.join("\n");
}

export function renderMaxMarkdown(text: string): string {
  return renderListsWithHardBreak(stripThematicBreaks(text));
}

function retryAfterMs(headers: Headers): number | undefined {
  const value = headers.get("retry-after");
  if (!value) {
    return;
  }
  const seconds = Number(value);
  if (Number.isFinite(seconds) && seconds >= 0) {
    return Math.ceil(seconds * 1000);
  }
  const date = Date.parse(value);
  return Number.isNaN(date) ? undefined : Math.max(0, date - Date.now());
}

function maxToken(): string {
  const token = env.MAX_BOT_TOKEN;
  if (!token) {
    throw new Error("MAX_BOT_TOKEN is not configured");
  }
  return token;
}

async function maxApiRequest(
  path: string,
  body: Record<string, unknown> | undefined,
  method: "DELETE" | "PATCH" | "POST" | "PUT" = "POST"
): Promise<void> {
  const response = await undiciFetch(`${MAX_API_BASE_URL}${path}`, {
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    dispatcher: await getMaxDispatcher(),
    headers: {
      Authorization: maxToken(),
      "Content-Type": "application/json",
    },
    method,
  });
  const failureRetryAfterMs = retryAfterMs(response.headers);
  // Ответ API нам не нужен; закрываем body, чтобы transport мог освободить соединение.
  await response.body?.cancel();
  if (!response.ok) {
    throw new MaxApiError(response.status, failureRetryAfterMs);
  }
}

/** Как maxApiRequest, но возвращает тело ответа — нужно для id статус-сообщения. */
async function maxApiRequestJson(
  path: string,
  body: Record<string, unknown>
): Promise<unknown> {
  const response = await undiciFetch(`${MAX_API_BASE_URL}${path}`, {
    body: JSON.stringify(body),
    dispatcher: await getMaxDispatcher(),
    headers: {
      Authorization: maxToken(),
      "Content-Type": "application/json",
    },
    method: "POST",
  });
  if (!response.ok) {
    const failureRetryAfterMs = retryAfterMs(response.headers);
    await response.body?.cancel();
    throw new MaxApiError(response.status, failureRetryAfterMs);
  }
  return response.json();
}

/**
 * Статус-сообщение хода («Ищу в базе знаний…»): одно на ход, тихое, правится
 * по этапам и удаляется на flush — финал приходит новым сообщением через
 * outbox. Машина состояний общая с Telegram (`createStatusMessageProgress`).
 * Ни лейбл, ни накопленный `text-delta` не шлём с `format: "markdown"` —
 * PUT-правка одного и того же сообщения меняет только переданные поля
 * (см. `editMarkupMessageId` выше), так что если бы `format` был выставлен
 * на send() и опущен на последующем edit(), нет гарантии, что MAX сбрасывает
 * его в plain, а не наследует markdown-парсинг от исходного сообщения.
 * Держим формат сообщения неизменным весь ход (никогда не markdown) — тогда
 * граница `text-delta` не может разорвать markdown-синтаксис ответа
 * (`**`, незакрытый `_`/`[`) и получить 4xx: канонический ответ и так
 * проходит полный `renderMaxMarkdown`+`splitForMax` при доставке через
 * outbox, здесь это лишь черновик без форматирования.
 */
function maxStatusMessageBody(content: string): Record<string, unknown> {
  return { notify: false, text: content };
}

const maxStatusProgress = createStatusMessageProgress(
  {
    async delete(_context, messageId) {
      await maxApiRequest(
        `/messages?message_id=${encodeURIComponent(messageId)}`,
        undefined,
        "DELETE"
      );
    },
    async edit(_context, messageId, content) {
      await maxApiRequest(
        `/messages?message_id=${encodeURIComponent(messageId)}`,
        maxStatusMessageBody(content),
        "PUT"
      );
    },
    async send(context, content) {
      const response = (await maxApiRequestJson(
        `/messages?user_id=${maxUserId(context.chatId)}`,
        maxStatusMessageBody(content)
      )) as { message?: { body?: { mid?: string } } };
      return response.message?.body?.mid;
    },
  },
  { editIntervalMs: PROGRESS_EDIT_INTERVAL_MS }
);

async function maxApiGet(path: string, signal: AbortSignal): Promise<unknown> {
  const response = await undiciFetch(`${MAX_API_BASE_URL}${path}`, {
    dispatcher: await getMaxDispatcher(),
    headers: { Authorization: maxToken() },
    method: "GET",
    signal,
  });
  if (!response.ok) {
    await response.body?.cancel();
    throw new MaxApiError(response.status, retryAfterMs(response.headers));
  }
  return response.json();
}

export function classifyMaxDeliveryError(
  error: unknown
): ChannelDeliveryFailure {
  if (
    error instanceof MaxApiError &&
    error.status >= 400 &&
    error.status < 500
  ) {
    return {
      errorCode: error.status,
      kind: "confirmed_rejection",
      ...(error.retryAfterMs === undefined
        ? {}
        : { retryAfterMs: error.retryAfterMs }),
    };
  }
  return { kind: "ambiguous" };
}

function maxUserId(chatId: string): string {
  if (!MAX_USER_ID_RE.test(chatId)) {
    throw new Error("MAX recipient id must be a positive integer");
  }
  return chatId;
}

/**
 * Клавиатура MAX — отрисовка финального `actions[]` (слой отрисовки action-модели,
 * `docs/channel-action-spec.md`; порядок и состав уже решены ядром `resolveAffordances`)
 * плюс отдельный ряд «новый диалог» ПОСЛЕДНИМ рядом. `link` → link-кнопка,
 * `callback` → callback-payload.
 *
 * Кнопка «новый диалог» (CRO-48, MAX-паритет) — в Telegram это persistent reply-клавиатура
 * под полем ввода: отдельный UI-слой, который виден ОДНОВРЕМЕННО с inline-кнопками конкретного
 * сообщения (согласие/рейтинг/…), а не вместо них. У MAX Bot API persistent-клавиатуры нет
 * вообще (только inline, привязанный к конкретному сообщению — dev.max.ru/docs-api), поэтому
 * чтобы получить тот же эффект, ряд «новый диалог» добавляется к ЛЮБОМУ набору кнопок —
 * и когда ядро прислало явный `actions[]` (рейтинг/эскалация/…), и в дефолте без него.
 * `payload` = обычный текст лейбла: клик шлёт боту этот текст, который ловит
 * `isNewDialogTrigger`, тем же путём, что и клик по persistent-кнопке в Telegram.
 */
/** Кнопка не нужна на явно подавленных ответах (`suppressReplyKeyboard` —
 * например, само подтверждение сброса), на деградированных (`degraded` —
 * «не удалось получить проверенный ответ, повторите позже») и на шагах
 * онбординга (`onboarding` — согласие/язык/роль/интро/тьюториал/complete,
 * `packages/core/src/index.ts`): это не полноценный ход диалога, предлагать
 * «начать новый» здесь не по адресу — пользователь ещё не завершил настройку,
 * сброс сессии посреди неё не имеет смысла (а собственные кнопки шага —
 * согласие/язык/роль — по-прежнему рисуются через `actions[]`). */
function shouldSuppressNewDialogButton(message: OutboundMessage): boolean {
  // biome-ignore lint/suspicious/noUnnecessaryConditions: message.meta ЯВЛЯЕТСЯ optional (OutboundMeta.optional() в contracts/schemas.ts) — ложное срабатывание на re-exported zod-типе
  const suppressReplyKeyboard = message.meta?.suppressReplyKeyboard;
  // biome-ignore lint/suspicious/noUnnecessaryConditions: message.meta ЯВЛЯЕТСЯ optional (OutboundMeta.optional() в contracts/schemas.ts) — ложное срабатывание на re-exported zod-типе
  const degraded = message.meta?.degraded;
  // biome-ignore lint/suspicious/noUnnecessaryConditions: message.meta ЯВЛЯЕТСЯ optional (OutboundMeta.optional() в contracts/schemas.ts) — ложное срабатывание на re-exported zod-типе
  const onboarding = message.meta?.onboarding;
  return Boolean(suppressReplyKeyboard || degraded || onboarding);
}

function newDialogButtonRow(
  message: OutboundMessage
): Record<string, unknown>[] {
  // biome-ignore lint/suspicious/noUnnecessaryConditions: message.meta ЯВЛЯЕТСЯ optional (OutboundMeta.optional() в contracts/schemas.ts) — ложное срабатывание на re-exported zod-типе
  const replyKeyboard = message.meta?.replyKeyboard;
  // biome-ignore lint/suspicious/noUnnecessaryConditions: message.meta ЯВЛЯЕТСЯ optional (OutboundMeta.optional() в contracts/schemas.ts) — ложное срабатывание на re-exported zod-типе
  const responseLanguage = message.meta?.responseLanguage;
  const label =
    replyKeyboard ?? NEW_DIALOG_BUTTON_LABEL[responseLanguage ?? "ru"];
  return [{ payload: label, text: label, type: "message" }];
}

/** `attachments[]` только с рядом «новый диалог» (или пустой массив, если явно
 * подавлен/деградирован) — используется при снятии одноразовых кнопок сообщения
 * (`send()`, `editMarkupMessageId`): рейтинг/consent убираются, этот ряд
 * остаётся, кроме `shouldSuppressNewDialogButton(message)`. */
function newDialogAttachments(
  message: OutboundMessage
): Record<string, unknown>[] {
  if (shouldSuppressNewDialogButton(message)) {
    return [];
  }
  return [
    {
      payload: { buttons: [newDialogButtonRow(message)] },
      type: "inline_keyboard",
    },
  ];
}

export function inlineKeyboard(
  message: OutboundMessage
): Record<string, unknown> | null {
  const rows: Record<string, unknown>[][] = [];
  if (message.actions?.length) {
    // По две кнопки в ряд — как в telegram: пара «Пауза | Удалить» читается одной строкой.
    for (const [index, action] of message.actions.entries()) {
      if (index % 2 === 0) {
        rows.push([]);
      }
      rows
        .at(-1)
        ?.push(
          action.kind === "link"
            ? { text: action.label, type: "link", url: action.url }
            : { payload: action.action, text: action.label, type: "callback" }
        );
    }
  }
  if (!shouldSuppressNewDialogButton(message)) {
    rows.push(newDialogButtonRow(message));
  }
  return rows.length
    ? { payload: { buttons: rows }, type: "inline_keyboard" }
    : null;
}

function extensionFor(contentType: string, url: string): string {
  if (contentType.includes("mpeg")) {
    return "mp3";
  }
  if (contentType.includes("mp4")) {
    return "m4a";
  }
  if (contentType.includes("ogg")) {
    return "ogg";
  }
  try {
    const extension = new URL(url).pathname.split(".").at(-1)?.toLowerCase();
    return extension && SAFE_EXTENSION_RE.test(extension) ? extension : "bin";
  } catch {
    return "bin";
  }
}

function safeAttachmentUrl(value: string): string {
  const url = new URL(value);
  if (url.protocol !== "https:") {
    throw new Error("MAX attachment URL must use HTTPS");
  }
  return url.toString();
}

async function downloadMaxProfileAvatar(value: string, signal: AbortSignal) {
  const avatarUrl = safeAttachmentUrl(value);
  const response = await undiciFetch(avatarUrl, {
    dispatcher: await getMaxDispatcher(),
    signal,
  });
  if (!response.ok) {
    throw new MaxApiError(response.status, retryAfterMs(response.headers));
  }
  const declaredBytes = Number(response.headers.get("content-length"));
  if (Number.isFinite(declaredBytes) && declaredBytes > MAX_FILE_BYTES) {
    throw new Error(`MAX profile avatar too large: ${declaredBytes} bytes`);
  }
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (bytes.byteLength > MAX_FILE_BYTES) {
    throw new Error(`MAX profile avatar too large: ${bytes.byteLength} bytes`);
  }
  const contentType =
    response.headers.get("content-type")?.split(";", 1)[0] ?? "image/jpeg";
  return {
    bytes,
    contentType,
    extension: extensionFor(contentType, avatarUrl),
    sourceId: createHash("sha256").update(bytes).digest("hex"),
  };
}

/**
 * Клавиатуру у сообщения с нажатой кнопкой снимаем ВСЕГДА, когда явно задан
 * editMarkupMessageId — через PUT /messages (MAX меняет только явно переданные
 * поля, text не передаём — остаётся прежним). Не только для silent-доставки
 * (оценка 👍/👎): consent/язык/онбординг/эскалация тоже шлют новый ответ с
 * editMarkupMessageId — иначе старая кнопка остаётся нажимаемой в истории
 * (паритет с Telegram — editMessageReplyMarkup). Снимаем именно оценочные/
 * одноразовые кнопки, а не клавиатуру целиком — «новый диалог» подставляем
 * на их место, иначе для silent-оценки (👍/👎, новое сообщение не шлётся)
 * кнопка исчезала бы из истории до следующего ответа бота.
 *
 * Обычный новый ответ (не клик по кнопке, editMarkupMessageId не задан) —
 * сообщение, что было последним с клавиатурой в этом чате, устарело: без
 * этого «новый диалог» копится под каждым прошлым ответом бота вместо того,
 * чтобы жить только на последнем (эмуляция persistent-клавиатуры Telegram
 * единственной кнопкой). ponytail: in-memory per-process, не переживает
 * рестарт/несколько реплик broadcast — максимум изредка не подчистит старую
 * кнопку; апгрейд — Redis-ключ на чат, если стейл-кнопки станут реальной
 * проблемой.
 */
async function reconcileStaleKeyboard(message: OutboundMessage): Promise<void> {
  if (message.editMarkupMessageId) {
    const attachments = newDialogAttachments(message);
    await maxApiRequest(
      `/messages?message_id=${encodeURIComponent(message.editMarkupMessageId)}`,
      { attachments },
      "PUT"
    ).catch(() => undefined);
    if (attachments.length > 0) {
      lastKeyboardMessageId.set(message.chatId, message.editMarkupMessageId);
    } else {
      lastKeyboardMessageId.delete(message.chatId);
    }
    return;
  }
  const staleMid = lastKeyboardMessageId.get(message.chatId);
  if (staleMid) {
    await maxApiRequest(
      `/messages?message_id=${encodeURIComponent(staleMid)}`,
      { attachments: [] },
      "PUT"
    ).catch(() => undefined);
    lastKeyboardMessageId.delete(message.chatId);
  }
}

/** Полноценный адаптер MAX Bot API для приватных диалогов. */
export const maxChannel: ChannelPort = {
  async acknowledgeInteraction(interactionId): Promise<void> {
    // Пустое тело MAX 400-тит: proto.payload "message or notification required".
    // Пустая строка проходит валидацию и не показывает пользователю тост.
    await maxApiRequest(
      `/answers?callback_id=${encodeURIComponent(interactionId)}`,
      { notification: "" }
    );
  },
  capabilities: {
    broadcast: true,
    inboundKinds: ["text", "voice", "image", "file"],
    interactions: true,
    markdown: true,
    outboundKinds: ["text"],
    progress: "status",
    replies: true,
    urlButtons: true,
  },
  classifyDeliveryError: classifyMaxDeliveryError,
  close: closeMaxAgent,

  async downloadInboundAttachment(message) {
    if (message.channel !== "max") {
      return null;
    }
    const raw = message.raw as MaxRawAttachment | undefined;
    if (!(raw?.attachmentUrl && raw.attachmentId)) {
      return null;
    }
    // URL вложения подписан MAX; bot token на сторонний CDN намеренно не передаём.
    const attachmentUrl = safeAttachmentUrl(raw.attachmentUrl);
    const response = await undiciFetch(attachmentUrl, {
      dispatcher: await getMaxDispatcher(),
    });
    if (!response.ok) {
      throw new MaxApiError(response.status, retryAfterMs(response.headers));
    }
    const declaredBytes = Number(response.headers.get("content-length"));
    if (Number.isFinite(declaredBytes) && declaredBytes > MAX_FILE_BYTES) {
      throw new Error(`MAX file too large: ${declaredBytes} bytes`);
    }
    const bytes = new Uint8Array(await response.arrayBuffer());
    if (bytes.byteLength > MAX_FILE_BYTES) {
      throw new Error(`MAX file too large: ${bytes.byteLength} bytes`);
    }
    const contentType =
      response.headers.get("content-type")?.split(";", 1)[0] ??
      "application/octet-stream";
    return {
      bytes,
      contentType,
      extension: extensionFor(contentType, attachmentUrl),
      sourceId: raw.attachmentId,
    };
  },

  isConfigured(): boolean {
    return Boolean(env.MAX_BOT_TOKEN && env.MAX_WEBHOOK_SECRET);
  },
  kind: "max",

  loadUserProfile(channelUserId) {
    const userId = maxUserId(channelUserId);
    return maxUserProfileResilient(async (signal) => {
      const payload = MaxChat.parse(
        await maxApiGet(`/chats/${encodeURIComponent(userId)}`, signal)
      );
      const user = payload.dialog_with_user;
      if (!user) {
        return null;
      }
      const avatarUrl = user.full_avatar_url ?? user.avatar_url;
      return {
        avatar: avatarUrl
          ? await downloadMaxProfileAvatar(avatarUrl, signal)
          : null,
        description: user.description ?? null,
        firstName: user.first_name ?? user.name ?? null,
        lastName: user.last_name ?? null,
        username: user.username ?? null,
      };
    });
  },

  normalize(payload): InboundMessage | null {
    const parsed = MaxTransportUpdate.safeParse(payload);
    if (!parsed.success) {
      return null;
    }
    const update = parsed.data;
    if (update.update_type === "message_callback") {
      return normalizeCallbackUpdate(update);
    }
    if (update.update_type === "bot_started") {
      return normalizeBotStartedUpdate(update);
    }
    return normalizeMessageUpdate(update);
  },

  publishProgress: maxStatusProgress.publish,

  async send(message: OutboundMessage): Promise<void> {
    await reconcileStaleKeyboard(message);
    // Тихая доставка (оценка 👍/👎): ACK callback уже ушёл из ingress, нового
    // сообщения не шлём.
    if (message.silent) {
      return;
    }
    const recipientId = maxUserId(message.chatId);
    const chunks = splitForMax(renderMaxMarkdown(message.text));
    const keyboard = inlineKeyboard(message);
    for (const [index, text] of chunks.entries()) {
      const isLast = index === chunks.length - 1;
      const body = {
        format: "markdown",
        // Уведомление несёт только первый чанк ответа; продолжения тихие.
        notify: index === 0,
        text,
        // "bot_started:<ts>" — синтетический id (см. normalizeBotStartedUpdate):
        // нажатие «Начать» не создаёт настоящего сообщения, MAX его как mid не
        // признаёт и отвечает 400 "Invalid message_id" на весь link.
        ...(index === 0 &&
        message.inReplyToMessageId &&
        !message.inReplyToMessageId.startsWith("bot_started:")
          ? { link: { mid: message.inReplyToMessageId, type: "reply" } }
          : {}),
        ...(isLast && keyboard ? { attachments: [keyboard] } : {}),
      };
      if (isLast && keyboard) {
        // Клавиатура на последнем чанке — запоминаем mid ответа API, чтобы
        // снять её со следующего нового сообщения в этом чате (см. выше).
        // oxlint-disable-next-line react-doctor/async-await-in-loop -- MAX должен получить чанки строго по порядку
        const response = (await maxApiRequestJson(
          `/messages?user_id=${recipientId}`,
          body
        )) as { message?: { body?: { mid?: string } } };
        const newMid = response.message?.body?.mid;
        if (newMid) {
          lastKeyboardMessageId.set(message.chatId, newMid);
        }
      } else {
        // oxlint-disable-next-line react-doctor/async-await-in-loop -- MAX должен получить чанки строго по порядку; параллельная отправка переставляет части ответа
        await maxApiRequest(`/messages?user_id=${recipientId}`, body);
      }
    }
  },

  /** MAX поддерживает одно глобальное меню без language scope, поэтому подписи трёхъязычные. */
  async syncCommands(): Promise<void> {
    await maxApiRequest(
      "/me/commands",
      { commands: [...MAX_BOT_COMMANDS] },
      "PATCH"
    );
  },

  verify(headers): VerifyResult {
    const secret = headers["x-max-bot-api-secret"];
    const verdict = verifySecretTokenVersioned(secret, {
      current: env.MAX_WEBHOOK_SECRET,
      previous: env.MAX_WEBHOOK_SECRET_PREVIOUS,
    });
    return {
      ok: verdict.ok,
      reason: verdict.ok ? undefined : "bad webhook secret",
      secretVersion: verdict.version,
    };
  },
  webhookAliases: ["max"],
};
