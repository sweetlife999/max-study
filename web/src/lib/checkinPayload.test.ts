import {
  extractCheckinFromScan,
  normalizeManualCode,
  parseCheckinStartParam,
} from './checkinPayload';

describe('parseCheckinStartParam', () => {
  it.each([
    ['ci_1_000000', { eventId: 1, code: '000000' }],
    ['ci_42_123456', { eventId: 42, code: '123456' }],
    ['ci_9007199254740991_999999', { eventId: 9007199254740991, code: '999999' }],
  ])('accepts %s', (value, expected) => {
    expect(parseCheckinStartParam(value)).toEqual(expected);
  });

  it.each([
    [null],
    [undefined],
    [''],
    ['ci'],
    ['ci_'],
    ['ci__123456'],
    ['ci_0_123456'],
    ['ci_01_123456'],
    ['ci_-1_123456'],
    ['ci_1.5_123456'],
    ['ci_abc_123456'],
    ['ci_1_12345'],
    ['ci_1_1234567'],
    ['ci_1_12a456'],
    ['ci_1_１２３４５６'],
    ['ci_1_٠١٢٣٤٥'],
    ['CI_1_123456'],
    ['ci_1_123456_'],
    ['ci_1_123456_extra'],
    [' ci_1_123456'],
    ['ci_1_123456 '],
    ['xci_1_123456'],
    ['ev_1'],
    ['org_token'],
    ['ci_9007199254740993_123456'],
    [`ci_1_123456${'x'.repeat(600)}`],
  ])('rejects %j', (value) => {
    expect(parseCheckinStartParam(value)).toBeNull();
  });
});

describe('extractCheckinFromScan', () => {
  it.each([
    ['https://max.ru/campus_bot?startapp=ci_7_654321', { eventId: 7, code: '654321' }],
    ['  https://max.ru/campus_bot?startapp=ci_7_654321\n', { eventId: 7, code: '654321' }],
    ['https://MAX.RU/campus_bot?startapp=ci_7_654321', { eventId: 7, code: '654321' }],
    ['https://web.max.ru/campus_bot?startapp=ci_7_654321', { eventId: 7, code: '654321' }],
    ['https://max.ru/campus_bot/?startapp=ci_7_654321&utm=x', { eventId: 7, code: '654321' }],
    ['https://max.ru/campus_bot?foo=1&startapp=ci_7_654321#frag', { eventId: 7, code: '654321' }],
    ['max.ru/campus_bot?startapp=ci_7_654321', { eventId: 7, code: '654321' }],
    ['ci_12_000123', { eventId: 12, code: '000123' }],
  ])('extracts from %j', (text, expected) => {
    expect(extractCheckinFromScan(text)).toEqual(expected);
  });

  it.each([
    [''],
    ['   '],
    ['123456'],
    ['hello world'],
    ['http://max.ru/campus_bot?startapp=ci_7_654321'],
    ['https://evil.example/campus_bot?startapp=ci_7_654321'],
    ['https://max.ru.evil.example/bot?startapp=ci_7_654321'],
    ['https://notmax.ru/bot?startapp=ci_7_654321'],
    ['https://max.ru/campus_bot?start=ci_7_654321'],
    ['https://max.ru/campus_bot?startapp=ev_7'],
    ['https://max.ru/campus_bot?startapp=ci_7_65432'],
    ['https://max.ru/campus_bot?startapp='],
    ['https://max.ru/campus_bot?startapp'],
    ['javascript:alert(1)'],
    ['https://max.ru/campus_bot?startapp=ci_0_654321'],
  ])('rejects %j', (text) => {
    expect(extractCheckinFromScan(text)).toBeNull();
  });
});

describe('normalizeManualCode', () => {
  it.each([
    ['123456', '123456'],
    ['000001', '000001'],
    ['123 456', '123456'],
    ['123-456', '123456'],
    [' 123456 ', '123456'],
  ])('normalizes %j', (input, expected) => {
    expect(normalizeManualCode(input)).toBe(expected);
  });

  it.each([[''], ['12345'], ['1234567'], ['12345a'], ['１２３４５６'], ['12.3456']])(
    'rejects %j',
    (input) => {
      expect(normalizeManualCode(input)).toBeNull();
    },
  );
});
