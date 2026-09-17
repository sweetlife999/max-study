import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@inna/config", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@inna/config")>();
  return {
    ...actual,
    env: {
      ...actual.env,
      ANSWER_SPLIT_MAX_CHARS: 1500,
      LOG_LEVEL: "silent",
      MAX_BOT_TOKEN: "max-token",
      MAX_WEBHOOK_SECRET: "max-webhook-secret",
      MAX_WEBHOOK_SECRET_PREVIOUS: "max-webhook-secret-old",
      OTEL_SERVICE_NAME: "channel-max-test",
    },
  };
});

const { closeAgent, fetchMock } = vi.hoisted(() => ({
  closeAgent: vi.fn(() => Promise.resolve()),
  fetchMock: vi.fn(),
}));
vi.mock("undici", () => ({
  Agent: class {
    close = closeAgent;
  },
  fetch: fetchMock,
}));

const {
  MaxApiError,
  classifyMaxDeliveryError,
  closeMaxAgent,
  maxChannel,
  renderMaxMarkdown,
  resetMaxKeyboardTrackingForTests,
  splitForMax,
} = await import("./index");

function messageUpdate(overrides: Record<string, unknown> = {}) {
  return {
    message: {
      body: { attachments: null, mid: "mid-1", text: "Привет" },
      recipient: { chat_id: null, chat_type: "dialog" },
      sender: {
        first_name: "Айгуль",
        is_bot: false,
        last_name: "Иванова",
        user_id: 42,
        username: "aigul",
      },
      ...overrides,
    },
    timestamp: 1_700_000_000_000,
    update_type: "message_created",
    user_locale: "ru-RU",
  };
}

function okResponse(extra: Partial<Response> = {}): Response {
  return {
    headers: new Headers(),
    json: () => Promise.resolve({ message: { body: { mid: "mock-mid" } } }),
    ok: true,
    status: 200,
    ...extra,
  } as Response;
}

describe("maxChannel.verify", () => {
  it("сверяет официальный X-Max-Bot-Api-Secret fail-closed", () => {
    expect(
      maxChannel.verify({ "x-max-bot-api-secret": "max-webhook-secret" }, "").ok
    ).toBe(true);
    expect(maxChannel.verify({ "x-max-bot-api-secret": "wrong" }, "").ok).toBe(
      false
    );
    expect(maxChannel.verify({}, "").ok).toBe(false);
  });

  it("PT-01: секрет предыдущей версии тоже проходит (ротация с перекрытием), с меткой version=previous", () => {
    const verdict = maxChannel.verify(
      { "x-max-bot-api-secret": "max-webhook-secret-old" },
      ""
    );
    expect(verdict.ok).toBe(true);
    expect(verdict.secretVersion).toBe("previous");
  });

  it("PT-01: текущий секрет помечается version=current", () => {
    const verdict = maxChannel.verify(
      { "x-max-bot-api-secret": "max-webhook-secret" },
      ""
    );
    expect(verdict.secretVersion).toBe("current");
  });
});

describe("maxChannel.normalize", () => {
  it("нормализует приватный текст и locale", () => {
    expect(maxChannel.normalize(messageUpdate())).toMatchObject({
      channel: "max",
      channelUserId: "42",
      chatId: "42",
      idempotencyKey: "max:mid-1",
      kind: "text",
      locale: "ru",
      messageId: "mid-1",
      senderFirstName: "Айгуль",
      senderLastName: "Иванова",
      senderUsername: "aigul",
      text: "Привет",
    });
  });

  it("audio attachment становится voice с отложенной ссылкой", () => {
    const inbound = maxChannel.normalize(
      messageUpdate({
        body: {
          attachments: [
            {
              payload: { token: "t", url: "https://cdn.max.ru/voice.ogg" },
              type: "audio",
            },
          ],
          mid: "voice-1",
          text: "подпись",
        },
        recipient: { chat_id: null, chat_type: "dialog" },
        sender: { is_bot: false, user_id: 42 },
      })
    );
    expect(inbound).toMatchObject({
      kind: "voice",
      raw: {
        attachmentId: "voice-1",
        attachmentType: "audio",
        attachmentUrl: "https://cdn.max.ru/voice.ogg",
      },
      text: "подпись",
    });
  });

  it("выбирает image и file после приоритетной проверки attachment-типов", () => {
    const image = maxChannel.normalize(
      messageUpdate({
        body: {
          attachments: [
            { payload: { url: "https://cdn.max.ru/a.bin" }, type: "file" },
            { payload: { url: "https://cdn.max.ru/a.jpg" }, type: "image" },
          ],
          mid: "image-1",
          text: null,
        },
      })
    );
    const file = maxChannel.normalize(
      messageUpdate({
        body: {
          attachments: [
            { payload: { url: "https://cdn.max.ru/a.pdf" }, type: "file" },
          ],
          mid: "file-1",
          text: null,
        },
      })
    );

    expect(image?.kind).toBe("image");
    expect(file?.kind).toBe("file");
  });

  it("consent callback превращается в общую команду с interactionId", () => {
    const inbound = maxChannel.normalize({
      callback: {
        callback_id: "cb-1",
        payload: "consent:accept",
        user: { is_bot: false, user_id: 7 },
      },
      message: {
        body: { attachments: null, mid: "prompt-1", text: "Согласие" },
        recipient: { chat_id: null, chat_type: "dialog" },
      },
      timestamp: 1,
      update_type: "message_callback",
      user_locale: "en-US",
    });
    expect(inbound).toMatchObject({
      chatId: "7",
      idempotencyKey: "max:callback:cb-1",
      interactionId: "cb-1",
      locale: "en",
      messageId: "prompt-1",
      text: "/consent",
    });
  });

  it.each([
    ["operator:request", "/operator"],
    ["sched:pause:aaaaaaaa", "/schedule_pause aaaaaaaa"],
    ["sched:confirm:A1B2C3D4E5F6", "/schedule_confirm A1B2C3D4E5F6"],
    ["ask:012345abcdef:3", "/ask 012345abcdef 3"],
    [
      "profile:confirm:residence:00000000-0000-4000-8000-000000000001",
      "/profile_confirm residence 00000000-0000-4000-8000-000000000001",
    ],
    [
      "profile:reject:interest:00000000-0000-4000-8000-000000000001",
      "/profile_reject interest 00000000-0000-4000-8000-000000000001",
    ],
  ])("callback %s → команда %s (паритет с Telegram)", (payload, command) => {
    const inbound = maxChannel.normalize({
      callback: {
        callback_id: "cb-2",
        payload,
        user: { is_bot: false, user_id: 7 },
      },
      message: {
        body: { attachments: null, mid: "prompt-2", text: "…" },
        recipient: { chat_id: null, chat_type: "dialog" },
      },
      timestamp: 1,
      update_type: "message_callback",
      user_locale: "ru-RU",
    });
    expect(inbound?.text).toBe(command);
  });

  it("bot_started синтезирует /start — первое нажатие «Начать» без сообщения", () => {
    const inbound = maxChannel.normalize({
      chat_id: 77,
      timestamp: 1_700_000_000,
      update_type: "bot_started",
      user: {
        first_name: "Айгуль",
        is_bot: false,
        last_name: "Иванова",
        user_id: 42,
        username: "aigul",
      },
      user_locale: "ru-RU",
    });
    expect(inbound).toMatchObject({
      channel: "max",
      channelUserId: "42",
      chatId: "42",
      idempotencyKey: "max:start:42:1700000000",
      kind: "text",
      locale: "ru",
      senderFirstName: "Айгуль",
      text: "/start",
    });
  });

  it("bot_started от бота игнорируется", () => {
    expect(
      maxChannel.normalize({
        chat_id: 77,
        timestamp: 1,
        update_type: "bot_started",
        user: { is_bot: true, user_id: 42 },
      })
    ).toBeNull();
  });

  it("игнорирует группы, ботов, неизвестные callback и битый payload", () => {
    expect(
      maxChannel.normalize(
        messageUpdate({
          body: { mid: "m", text: "group" },
          recipient: { chat_id: 1, chat_type: "chat" },
          sender: { is_bot: false, user_id: 42 },
        })
      )
    ).toBeNull();
    expect(
      maxChannel.normalize(
        messageUpdate({
          body: { mid: "m", text: "bot" },
          recipient: { chat_id: null, chat_type: "dialog" },
          sender: { is_bot: true, user_id: 42 },
        })
      )
    ).toBeNull();
    expect(maxChannel.normalize({ update_type: "unknown" })).toBeNull();
  });
});

describe("maxChannel.send", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    fetchMock.mockResolvedValue(okResponse());
    resetMaxKeyboardTrackingForTests();
  });

  it("отправляет Markdown, reply и URL-кнопку через официальный API", async () => {
    await maxChannel.send({
      actions: [
        { kind: "link", label: "Открыть", url: "https://innopolis.ru" },
      ],
      channel: "max",
      chatId: "42",
      inReplyToMessageId: "source-mid",
      kind: "text",
      text: "**Новость**",
    });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://platform-api2.max.ru/messages?user_id=42");
    expect(init.headers).toMatchObject({ Authorization: "max-token" });
    expect(JSON.parse(String(init.body))).toEqual({
      attachments: [
        {
          payload: {
            buttons: [
              [
                {
                  text: "Открыть",
                  type: "link",
                  url: "https://innopolis.ru",
                },
              ],
              [
                {
                  payload: "Начать новый диалог",
                  text: "Начать новый диалог",
                  type: "message",
                },
              ],
            ],
          },
          type: "inline_keyboard",
        },
      ],
      format: "markdown",
      link: { mid: "source-mid", type: "reply" },
      notify: true,
      text: "**Новость**",
    });
  });

  it("превращает Markdown-список в MAX-safe bullets с жёсткими переносами", async () => {
    await maxChannel.send({
      channel: "max",
      chatId: "42",
      kind: "text",
      text: "Где поесть:\n- Happiness — завтраки\n- Geek Cafe — кофе\n1) CAVA",
    });

    const body = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body));
    expect(body.text).toBe(
      "Где поесть:  \n• Happiness — завтраки  \n• Geek Cafe — кофе  \n1) CAVA"
    );
  });

  it("не меняет маркеры внутри fenced code", () => {
    expect(renderMaxMarkdown("```text\n- literal\n```\n- пункт")).toBe(
      "```text\n- literal\n```  \n• пункт"
    );
  });

  it("вырезает горизонтальные разделители — MAX их не поддерживает и рендерит буквально", () => {
    expect(renderMaxMarkdown("Абзац один\n\n---\n\nАбзац два")).toBe(
      "Абзац один\n\n\nАбзац два"
    );
    expect(renderMaxMarkdown("Текст\n***\nЕщё текст\n___")).toBe(
      "Текст\nЕщё текст"
    );
  });

  it("не трогает похожую на разделитель строку внутри fenced code", () => {
    expect(renderMaxMarkdown("```text\n---\n```")).toBe("```text\n---\n```");
  });

  it("не подставляет link.mid для синтетического id bot_started (MAX его 400-тит)", async () => {
    await maxChannel.send({
      channel: "max",
      chatId: "42",
      inReplyToMessageId: "bot_started:1700000000",
      kind: "text",
      text: "Привет!",
    });

    const body = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body));
    expect(body.link).toBeUndefined();
  });

  it("consent использует те же action-id, что Telegram", async () => {
    await maxChannel.send({
      // Ядро (resolveAffordances) уже свернуло consentRequired в actions[]; канал их рисует.
      actions: [
        { action: "consent:accept", kind: "callback", label: "✅ Согласиться" },
        { action: "consent:decline", kind: "callback", label: "Не сейчас" },
      ],
      channel: "max",
      chatId: "42",
      kind: "text",
      text: "Нужно согласие",
    });
    const body = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body));
    expect(body.attachments[0].payload.buttons[0]).toMatchObject([
      { payload: "consent:accept", type: "callback" },
      { payload: "consent:decline", type: "callback" },
    ]);
  });

  it("рисует кнопку оператора по meta.operatorOffer (паритет с Telegram)", async () => {
    await maxChannel.send({
      // operatorOffer тоже свёрнут ядром в actions[] — канал не читает meta ради кнопок.
      actions: [
        {
          action: "operator:request",
          kind: "callback",
          label: "👤 Связаться с оператором",
        },
      ],
      channel: "max",
      chatId: "42",
      kind: "text",
      text: "Похоже, нужен человек.",
    });
    const body = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body));
    expect(body.attachments[0].payload.buttons).toEqual([
      [
        {
          payload: "operator:request",
          text: "👤 Связаться с оператором",
          type: "callback",
        },
      ],
      [
        {
          payload: "Начать новый диалог",
          text: "Начать новый диалог",
          type: "message",
        },
      ],
    ]);
  });

  it("CRO-48: meta.replyKeyboard рисует message-кнопку (MAX-эквивалент persistent клавиатуры)", async () => {
    await maxChannel.send({
      channel: "max",
      chatId: "42",
      kind: "text",
      meta: { replyKeyboard: "Начать новый диалог" },
      text: "Готово!",
    });
    const body = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body));
    expect(body.attachments[0].payload.buttons).toEqual([
      [
        {
          payload: "Начать новый диалог",
          text: "Начать новый диалог",
          type: "message",
        },
      ],
    ]);
  });

  it("CRO-48: actions[] рисуются первыми рядами, «новый диалог» — последним рядом", async () => {
    await maxChannel.send({
      actions: [
        { action: "consent:accept", kind: "callback", label: "✅ Согласиться" },
      ],
      channel: "max",
      chatId: "42",
      kind: "text",
      meta: { replyKeyboard: "Начать новый диалог" },
      text: "Нужно согласие",
    });
    const body = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body));
    expect(body.attachments[0].payload.buttons).toEqual([
      [{ payload: "consent:accept", text: "✅ Согласиться", type: "callback" }],
      [
        {
          payload: "Начать новый диалог",
          text: "Начать новый диалог",
          type: "message",
        },
      ],
    ]);
  });

  it("meta.suppressReplyKeyboard подавляет кнопку «новый диалог» (подтверждение сброса не зовёт сама себя)", async () => {
    await maxChannel.send({
      channel: "max",
      chatId: "42",
      kind: "text",
      meta: { suppressReplyKeyboard: true },
      text: "🆕 Начинаю новый диалог.",
    });
    const body = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body));
    expect(body.attachments).toBeUndefined();
  });

  it("meta.degraded тоже подавляет кнопку «новый диалог» (не удалось получить ответ — нужен повтор запроса, не новый диалог)", async () => {
    await maxChannel.send({
      channel: "max",
      chatId: "42",
      kind: "text",
      meta: { degraded: true },
      text: "Сейчас не удалось получить проверенный ответ. Пожалуйста, повторите запрос чуть позже.",
    });
    const body = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body));
    expect(body.attachments).toBeUndefined();
  });

  it("meta.onboarding подавляет «новый диалог», но сохраняет собственные кнопки шага (согласие/язык/роль)", async () => {
    await maxChannel.send({
      actions: [
        { action: "consent:accept", kind: "callback", label: "✅ Согласиться" },
        { action: "consent:decline", kind: "callback", label: "Не сейчас" },
      ],
      channel: "max",
      chatId: "42",
      kind: "text",
      meta: { consentRequired: true, onboarding: true },
      text: "Нужно ваше согласие на обработку персональных данных.",
    });
    const body = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body));
    expect(body.attachments[0].payload.buttons).toEqual([
      [
        { payload: "consent:accept", text: "✅ Согласиться", type: "callback" },
        { payload: "consent:decline", text: "Не сейчас", type: "callback" },
      ],
    ]);
  });

  it("новое сообщение (не клик по кнопке) снимает «новый диалог» с предыдущего ответа в этом чате", async () => {
    await maxChannel.send({
      channel: "max",
      chatId: "42",
      kind: "text",
      text: "Привет! Чем помочь?",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await maxChannel.send({
      channel: "max",
      chatId: "42",
      kind: "text",
      text: "Отвечаю на второй вопрос.",
    });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    // Первый вызов второго send() — снятие клавиатуры с mid первого ответа.
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "https://platform-api2.max.ru/messages?message_id=mock-mid"
    );
    const stripInit = fetchMock.mock.calls[1]?.[1] as RequestInit;
    expect(stripInit.method).toBe("PUT");
    expect(JSON.parse(String(stripInit.body))).toEqual({ attachments: [] });
    // Второй вызов — сам новый ответ, снова с «новый диалог».
    expect(fetchMock.mock.calls[2]?.[0]).toBe(
      "https://platform-api2.max.ru/messages?user_id=42"
    );
  });

  it("рисует кнопки расписаний по две в ряд", async () => {
    await maxChannel.send({
      actions: [
        { action: "sched:pause:aaaaaaaa", kind: "callback", label: "⏸ Утро" },
        { action: "sched:delete:aaaaaaaa", kind: "callback", label: "🗑 Утро" },
        { action: "sched:resume:bbbbbbbb", kind: "callback", label: "▶️ Вечер" },
      ],
      channel: "max",
      chatId: "42",
      kind: "text",
      text: "Ваши расписания:",
    });
    const body = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body));
    expect(body.attachments[0].payload.buttons).toEqual([
      [
        { payload: "sched:pause:aaaaaaaa", text: "⏸ Утро", type: "callback" },
        { payload: "sched:delete:aaaaaaaa", text: "🗑 Утро", type: "callback" },
      ],
      [
        {
          payload: "sched:resume:bbbbbbbb",
          text: "▶️ Вечер",
          type: "callback",
        },
      ],
      [
        {
          payload: "Начать новый диалог",
          text: "Начать новый диалог",
          type: "message",
        },
      ],
    ]);
  });

  it("рисует ask_user callback без серверных resume-полей", async () => {
    await maxChannel.send({
      actions: [
        {
          action: "ask:012345abcdef:1",
          kind: "callback",
          label: "1. В Казань",
          toolCallId: "ask-call-1",
          value: "В Казань",
        },
        {
          action: "ask:012345abcdef:2",
          kind: "callback",
          label: "2. В Иннополис",
          toolCallId: "ask-call-1",
          value: "В Иннополис",
        },
      ],
      channel: "max",
      chatId: "42",
      kind: "text",
      text: "Куда едем?",
    });
    const body = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body));
    expect(body.attachments[0].payload.buttons).toEqual([
      [
        {
          payload: "ask:012345abcdef:1",
          text: "1. В Казань",
          type: "callback",
        },
        {
          payload: "ask:012345abcdef:2",
          text: "2. В Иннополис",
          type: "callback",
        },
      ],
      [
        {
          payload: "Начать новый диалог",
          text: "Начать новый диалог",
          type: "message",
        },
      ],
    ]);
  });

  it("длинный текст режет по продуктовому лимиту 1500 (ТЗ п.13.4), клавиатура только у последнего чанка", async () => {
    await maxChannel.send({
      actions: [
        { kind: "link", label: "Открыть", url: "https://innopolis.ru" },
      ],
      channel: "max",
      chatId: "42",
      kind: "text",
      text: "x".repeat(4000),
    });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    const bodies = fetchMock.mock.calls.map((call) =>
      JSON.parse(String((call[1] as RequestInit).body))
    );
    for (const body of bodies) {
      expect(body.text.length).toBeLessThanOrEqual(1500);
    }
    expect(bodies.map((body) => body.text.slice(-5))).toEqual([
      "(1/3)",
      "(2/3)",
      "(3/3)",
    ]);
    // Уведомление несёт только первый чанк, продолжения тихие.
    expect(bodies.map((body) => body.notify)).toEqual([true, false, false]);
    expect(bodies[0].attachments).toBeUndefined();
    expect(bodies[2].attachments).toHaveLength(1);
  });

  it("кнопки оценки уходят в attachments последнего чанка", async () => {
    await maxChannel.send({
      actions: [
        { action: "fb:like", kind: "callback", label: "👍 Полезно" },
        { action: "fb:dislike", kind: "callback", label: "👎 Не помогло" },
      ],
      channel: "max",
      chatId: "42",
      kind: "text",
      text: "Ответ агента",
    });
    const body = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body));
    expect(body.attachments[0].payload.buttons).toEqual([
      [
        { payload: "fb:like", text: "👍 Полезно", type: "callback" },
        { payload: "fb:dislike", text: "👎 Не помогло", type: "callback" },
      ],
      [
        {
          payload: "Начать новый диалог",
          text: "Начать новый диалог",
          type: "message",
        },
      ],
    ]);
  });

  it("silent-доставка без editMarkupMessageId не шлёт ни одного сообщения", async () => {
    await maxChannel.send({
      channel: "max",
      chatId: "42",
      interactionId: "cb-1",
      kind: "text",
      silent: true,
      text: "",
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("silent-доставка с editMarkupMessageId меняет оценочные кнопки на «новый диалог» и не шлёт текст", async () => {
    await maxChannel.send({
      channel: "max",
      chatId: "42",
      editMarkupMessageId: "mid-1",
      interactionId: "cb-1",
      kind: "text",
      silent: true,
      text: "",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "https://platform-api2.max.ru/messages?message_id=mid-1"
    );
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(init.method).toBe("PUT");
    const body = JSON.parse(String(init.body));
    expect(body.attachments[0].payload.buttons).toEqual([
      [
        {
          payload: "Начать новый диалог",
          text: "Начать новый диалог",
          type: "message",
        },
      ],
    ]);
  });

  it("не-silent ответ на кнопку (consent/язык/эскалация) тоже меняет старую клавиатуру на «новый диалог» и шлёт новый текст", async () => {
    await maxChannel.send({
      channel: "max",
      chatId: "42",
      editMarkupMessageId: "prompt-1",
      interactionId: "cb-2",
      kind: "text",
      text: "Спасибо, продолжаем",
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "https://platform-api2.max.ru/messages?message_id=prompt-1"
    );
    const editInit = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(editInit.method).toBe("PUT");
    const editBody = JSON.parse(String(editInit.body));
    expect(editBody.attachments[0].payload.buttons).toEqual([
      [
        {
          payload: "Начать новый диалог",
          text: "Начать новый диалог",
          type: "message",
        },
      ],
    ]);
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "https://platform-api2.max.ru/messages?user_id=42"
    );
    const sendInit = fetchMock.mock.calls[1]?.[1] as RequestInit;
    const sendBody = JSON.parse(String(sendInit.body));
    expect(sendBody.text).toBe("Спасибо, продолжаем");
  });

  it("callback ACK вызывает /answers до тяжёлой обработки", async () => {
    await maxChannel.acknowledgeInteraction?.("cb/1");
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "https://platform-api2.max.ru/answers?callback_id=cb%2F1"
    );
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const body = JSON.parse(String(init.body));
    expect(body).toEqual({ notification: "" });
  });
});

describe("maxChannel.publishProgress (status message)", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.useFakeTimers();
    resetMaxKeyboardTrackingForTests();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  function bodyOf(index: number): Record<string, unknown> {
    const call = fetchMock.mock.calls[index] as [string, RequestInit];
    return JSON.parse(String(call[1].body));
  }

  it("одно тихое статус-сообщение: создаётся на tool-этапе, правится, удаляется на flush; финал — новое сообщение", async () => {
    fetchMock
      .mockResolvedValueOnce(
        okResponse({
          json: () =>
            Promise.resolve({ message: { body: { mid: "status-mid" } } }),
        })
      )
      .mockResolvedValue(okResponse());
    const context = {
      chatId: "42",
      inReplyToMessageId: "77",
      locale: "ru",
      turnId: "77",
    };

    await maxChannel.publishProgress?.(context, {
      phase: "thinking",
      type: "status",
    });
    expect(fetchMock).not.toHaveBeenCalled();
    await maxChannel.publishProgress?.(context, {
      phase: "searching",
      type: "status",
    });
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "https://platform-api2.max.ru/messages?user_id=42"
    );
    expect(bodyOf(0)).toEqual({
      notify: false,
      text: "Ищу в базе знаний…",
    });

    await vi.advanceTimersByTimeAsync(600);
    // text-delta приоритетнее лейбла этапа: как только пошёл текст ответа,
    // статус-сообщение переключается на него и не возвращается к лейблу.
    await maxChannel.publishProgress?.(context, {
      text: "Черновик ответа",
      type: "text-delta",
    });
    const edit = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(edit[0]).toBe(
      "https://platform-api2.max.ru/messages?message_id=status-mid"
    );
    expect(edit[1].method).toBe("PUT");
    // Накопленный текст ответа шлётся plain-текстом (без format: markdown) —
    // граница дельты может разорвать markdown ответа посередине.
    expect(bodyOf(1)).toEqual({
      notify: false,
      text: "Черновик ответа",
    });

    // Статус-этап после того, как пошёл текст, уже не должен затирать его.
    await maxChannel.publishProgress?.(context, {
      phase: "composing",
      type: "status",
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);

    await maxChannel.publishProgress?.(context, { type: "flush" });
    const del = fetchMock.mock.calls[2] as [string, RequestInit];
    expect(del[0]).toBe(
      "https://platform-api2.max.ru/messages?message_id=status-mid"
    );
    expect(del[1].method).toBe("DELETE");
    expect(del[1].body).toBeUndefined();

    await maxChannel.send({
      channel: "max",
      chatId: "42",
      inReplyToMessageId: "77",
      kind: "text",
      text: "Канонический ответ",
    });
    expect(fetchMock.mock.calls[3]?.[0]).toBe(
      "https://platform-api2.max.ru/messages?user_id=42"
    );
    expect(bodyOf(3)).toMatchObject({
      notify: true,
      text: "Канонический ответ",
    });
  });

  it("text-delta с markdown-ломающими символами не шлётся как format: markdown", async () => {
    fetchMock
      .mockResolvedValueOnce(
        okResponse({
          json: () => Promise.resolve({ message: { body: { mid: "mid" } } }),
        })
      )
      .mockResolvedValue(okResponse());
    const context = { chatId: "42", locale: "ru", turnId: "delta" };

    // Первая дельта без предшествующего "status" ждёт ту же thinkingDelayMs
    // задержку, что и "Думаю…" — короткие ходы не мигают статусом.
    await maxChannel.publishProgress?.(context, {
      text: "Ответ про **жирный",
      type: "text-delta",
    });
    expect(fetchMock).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1200);

    // Обрыв дельты посреди "**жирный" и одиночный "_" — валидный markdown
    // здесь не гарантирован, только plain text спасает от 4xx MAX.
    expect(bodyOf(0)).toEqual({
      notify: false,
      text: "Ответ про **жирный",
    });

    await vi.advanceTimersByTimeAsync(600);
    await maxChannel.publishProgress?.(context, {
      text: "_ текст",
      type: "text-delta",
    });
    expect(bodyOf(1)).toEqual({
      notify: false,
      text: "Ответ про **жирный_ текст",
    });

    await maxChannel.publishProgress?.(context, { type: "flush" });
  });

  it("долгий thinking без tools показывает «Думаю…» после задержки", async () => {
    fetchMock.mockResolvedValue(
      okResponse({
        json: () => Promise.resolve({ message: { body: { mid: "slow-mid" } } }),
      })
    );
    const context = { chatId: "42", locale: "ru", turnId: "slow" };
    await maxChannel.publishProgress?.(context, {
      phase: "thinking",
      type: "status",
    });
    await vi.advanceTimersByTimeAsync(1199);
    expect(fetchMock).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(bodyOf(0)).toMatchObject({ notify: false, text: "Думаю…" });
    await maxChannel.publishProgress?.(context, { type: "flush" });
    expect((fetchMock.mock.calls[1] as [string, RequestInit])[1].method).toBe(
      "DELETE"
    );
  });

  it("правки статуса не чаще двух в секунду: быстрые смены этапов коалесцируются", async () => {
    fetchMock.mockResolvedValue(
      okResponse({
        json: () => Promise.resolve({ message: { body: { mid: "m" } } }),
      })
    );
    const context = { chatId: "42", locale: "ru", turnId: "burst" };
    await maxChannel.publishProgress?.(context, {
      phase: "searching",
      type: "status",
    });
    await maxChannel.publishProgress?.(context, {
      phase: "composing",
      type: "status",
    });
    await maxChannel.publishProgress?.(context, {
      phase: "ticket",
      type: "status",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(549);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(bodyOf(1)).toMatchObject({ text: "Работаю с обращением…" });
    await maxChannel.publishProgress?.(context, { type: "flush" });
  });

  it("send() без progress шлёт обычное новое сообщение", async () => {
    fetchMock.mockResolvedValueOnce(okResponse());

    await maxChannel.send({
      channel: "max",
      chatId: "42",
      kind: "text",
      text: "Без статуса",
    });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://platform-api2.max.ru/messages?user_id=42");
    expect(init.method ?? "POST").toBe("POST");
  });
});

describe("MAX media/error helpers", () => {
  beforeEach(() => fetchMock.mockReset());

  it("скачивает attachment с лимитом и content type", async () => {
    fetchMock.mockResolvedValue(
      okResponse({
        arrayBuffer: () => Promise.resolve(new Uint8Array([1, 2, 3]).buffer),
        headers: new Headers({
          "content-length": "3",
          "content-type": "audio/ogg; codecs=opus",
        }),
      })
    );
    const result = await maxChannel.downloadInboundAttachment?.({
      channel: "max",
      channelUserId: "42",
      chatId: "42",
      idempotencyKey: "max:voice-1",
      kind: "voice",
      locale: "ru",
      raw: {
        attachmentId: "voice-1",
        attachmentType: "audio",
        attachmentUrl: "https://cdn.max.ru/voice.ogg",
      },
      receivedAt: 1,
    });
    expect(result).toMatchObject({
      contentType: "audio/ogg",
      extension: "ogg",
      sourceId: "voice-1",
    });
    expect(Array.from(result?.bytes ?? [])).toEqual([1, 2, 3]);
  });

  it("загружает расширенный профиль диалога и аватар", async () => {
    fetchMock
      .mockResolvedValueOnce(
        okResponse({
          json: () =>
            Promise.resolve({
              dialog_with_user: {
                description: "Жительница Иннополиса",
                first_name: "Айгуль",
                full_avatar_url: "https://cdn.max.ru/avatar.jpg",
                last_name: "Иванова",
                user_id: 42,
                username: "aigul",
              },
            }),
        })
      )
      .mockResolvedValueOnce(
        okResponse({
          arrayBuffer: () => Promise.resolve(new Uint8Array([1, 2, 3]).buffer),
          headers: new Headers({
            "content-length": "3",
            "content-type": "image/jpeg",
          }),
        })
      );

    const profile = await maxChannel.loadUserProfile?.("42");

    expect(profile).toMatchObject({
      avatar: {
        contentType: "image/jpeg",
        extension: "jpg",
      },
      description: "Жительница Иннополиса",
      firstName: "Айгуль",
      lastName: "Иванова",
      username: "aigul",
    });
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "https://platform-api2.max.ru/chats/42",
      "https://cdn.max.ru/avatar.jpg",
    ]);
  });

  it("классифицирует 429 с retry-after и network/5xx как ambiguous", () => {
    expect(classifyMaxDeliveryError(new MaxApiError(429, 2000))).toEqual({
      errorCode: 429,
      kind: "confirmed_rejection",
      retryAfterMs: 2000,
    });
    expect(classifyMaxDeliveryError(new MaxApiError(500))).toEqual({
      kind: "ambiguous",
    });
    expect(classifyMaxDeliveryError(new Error("network"))).toEqual({
      kind: "ambiguous",
    });
  });

  it("capabilities описывают паритет и broadcast", () => {
    expect(maxChannel.isConfigured()).toBe(true);
    expect(maxChannel.capabilities).toMatchObject({
      broadcast: true,
      interactions: true,
      markdown: true,
      progress: "status",
      replies: true,
      urlButtons: true,
    });
    // Продуктовый лимит ТЗ п.13.4 (1500) сильнее транспортного 4000.
    const parts = splitForMax("x".repeat(4001));
    expect(parts).toHaveLength(3);
    for (const part of parts) {
      expect(part.length).toBeLessThanOrEqual(1500);
    }
  });

  it("закрывает общий TLS dispatcher при graceful shutdown", async () => {
    fetchMock.mockResolvedValue(okResponse());
    await maxChannel.acknowledgeInteraction?.("init-agent");
    await closeMaxAgent();
    expect(closeAgent).toHaveBeenCalledOnce();
  });
});

describe("maxChannel.syncCommands", () => {
  it("регистрирует глобальное меню с выбором языка", async () => {
    fetchMock.mockReset();
    fetchMock.mockResolvedValue(okResponse());

    await maxChannel.syncCommands?.();

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://platform-api2.max.ru/me/commands");
    expect(init.method).toBe("PATCH");
    const body = JSON.parse(String(init.body)) as {
      commands: { description: string; name: string }[];
    };
    expect(body.commands.map((command) => command.name)).toEqual([
      "start",
      "help",
      "language",
      "clear",
      "new",
      "schedule_list",
      "stop",
    ]);
    expect(
      body.commands.find((command) => command.name === "language")?.description
    ).toContain("Language");
  });
});
