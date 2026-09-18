import { INVALID_CHECKIN_ROUTE, startParamRoute } from './startParam';

describe('startParamRoute', () => {
  it.each([
    ['ci_1_000000', '/checkin/qr/1/000000'],
    ['ci_42_123456', '/checkin/qr/42/123456'],
    ['ci_9007199254740991_999999', '/checkin/qr/9007199254740991/999999'],
  ])('routes %s to the automatic check-in', (startParam, route) => {
    expect(startParamRoute(startParam)).toBe(route);
  });

  it.each([
    ['ci_'],
    ['ci_0_123456'],
    ['ci_01_123456'],
    ['ci_-1_123456'],
    ['ci_abc_123456'],
    ['ci_1_12345'],
    ['ci_1_1234567'],
    ['ci_1_12a456'],
    ['ci_1_123456_extra'],
    ['ci_1_١٢٣٤٥٦'],
    [`ci_1_123456${'x'.repeat(600)}`],
  ])('sends the unparsable check-in payload %s to the explaining screen', (startParam) => {
    expect(startParamRoute(startParam)).toBe(INVALID_CHECKIN_ROUTE);
  });

  it.each([
    [null],
    [undefined],
    [''],
    ['org_token'],
    ['ev_12'],
    ['promo_summer2025'],
    ['CI_1_123456'],
  ])('keeps the main screen for %s', (startParam) => {
    expect(startParamRoute(startParam)).toBeNull();
  });
});
