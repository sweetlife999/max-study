import { createContext, useContext } from 'react';

import type { Lang } from '../api/types';
import en from './en.json';
import ru from './ru.json';

export type MessageKey = keyof typeof ru;
export type MessageParams = Record<string, string | number>;
export type Translate = (key: MessageKey, params?: MessageParams) => string;

// Typing `en` as the full record makes a missing English key a compile error as well.
const DICTIONARIES: Record<Lang, Record<MessageKey, string>> = { ru, en };

export const SUPPORTED_LANGS: readonly Lang[] = ['ru', 'en'];

/** Maps a MAX `language_code` (e.g. "en-US") or any string to a supported language. */
export function resolveLang(value: string | null | undefined): Lang {
  return value?.toLowerCase().startsWith('en') ? 'en' : 'ru';
}

export function translate(lang: Lang, key: MessageKey, params?: MessageParams): string {
  const template = DICTIONARIES[lang][key];
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (placeholder, name: string) => {
    const value = params[name];
    return value === undefined ? placeholder : String(value);
  });
}

export interface I18nValue {
  lang: Lang;
  t: Translate;
}

export const I18nContext = createContext<I18nValue | null>(null);

export function useI18n(): I18nValue {
  const value = useContext(I18nContext);
  if (!value) throw new Error('useI18n must be used inside <I18nProvider>');
  return value;
}
