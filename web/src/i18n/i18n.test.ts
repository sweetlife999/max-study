import en from './en.json';
import { resolveLang, translate } from './i18n';
import ru from './ru.json';

const ruMap: Record<string, string> = ru;
const enMap: Record<string, string> = en;
const ruKeys = Object.keys(ruMap).sort();
const enKeys = Object.keys(enMap).sort();

describe('dictionaries', () => {
  it('declare exactly the same keys', () => {
    expect(enKeys).toEqual(ruKeys);
  });

  it('has no empty translation', () => {
    const empty = [...Object.entries(ruMap), ...Object.entries(enMap)].filter(
      ([, value]) => value.trim() === '',
    );
    expect(empty).toEqual([]);
  });

  it('uses the same placeholders in both languages', () => {
    const placeholders = (text: string) => [...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
    const mismatched = ruKeys.filter(
      (key) =>
        JSON.stringify(placeholders(ruMap[key] ?? '')) !==
        JSON.stringify(placeholders(enMap[key] ?? '')),
    );
    expect(mismatched).toEqual([]);
  });

  it('is used by every `t(...)` call in the source', async () => {
    // Guards against a key that exists in code but not in the dictionaries.
    const files = import.meta.glob('../**/*.{ts,tsx}', { query: '?raw', import: 'default' });
    const unknown = new Set<string>();
    for (const [path, load] of Object.entries(files)) {
      if (path.includes('/i18n/')) continue;
      const source = await load();
      for (const match of source.matchAll(/\bt\(\s*'([a-zA-Z][\w.]*)'/g)) {
        const key = match[1];
        if (key !== undefined && !(key in ruMap)) unknown.add(key);
      }
    }
    expect([...unknown]).toEqual([]);
  });
});

describe('translate', () => {
  it('substitutes named parameters', () => {
    expect(translate('ru', 'onboarding.progress', { done: 2, total: 5 })).toBe('2 из 5');
    expect(translate('en', 'onboarding.progress', { done: 2, total: 5 })).toBe('2 of 5');
  });

  it('leaves unknown placeholders untouched', () => {
    expect(translate('ru', 'home.title', {})).toBe('Привет, {name}!');
  });
});

describe('resolveLang', () => {
  it.each([
    ['en', 'en'],
    ['en-US', 'en'],
    ['EN-gb', 'en'],
    ['ru', 'ru'],
    ['ru-RU', 'ru'],
    ['kk', 'ru'],
    ['', 'ru'],
    [null, 'ru'],
    [undefined, 'ru'],
  ])('maps %s to %s', (input, expected) => {
    expect(resolveLang(input)).toBe(expected);
  });
});
