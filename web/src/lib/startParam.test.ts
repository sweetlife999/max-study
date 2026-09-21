import { INVALID_CHECKIN_ROUTE, startParamRoute } from './startParam';

describe('startParamRoute', () => {
  it.each([
    ['ci_1_000000', '/checkin/qr'],
    ['ci_42_123456', '/checkin/qr'],
    ['ci_9007199254740991_999999', '/checkin/qr'],
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
    ['ev_1', '/events/1'],
    ['ev_42', '/events/42'],
  ])('opens %s on its event card', (startParam, route) => {
    expect(startParamRoute(startParam)).toBe(route);
  });

  it.each([
    [null],
    [undefined],
    [''],
    ['org_token'],
    ['ev_0'],
    ['ev_01'],
    ['ev_9007199254740992'],
    ['promo_summer2025'],
    ['CI_1_123456'],
  ])('keeps the main screen for %s', (startParam) => {
    expect(startParamRoute(startParam)).toBeNull();
  });
});
