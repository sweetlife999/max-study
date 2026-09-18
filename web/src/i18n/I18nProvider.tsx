import { useMemo, type ReactNode } from 'react';

import type { Lang } from '../api/types';
import { I18nContext, translate, type I18nValue } from './i18n';

export function I18nProvider({ lang, children }: { lang: Lang; children: ReactNode }) {
  const value = useMemo<I18nValue>(
    () => ({ lang, t: (key, params) => translate(lang, key, params) }),
    [lang],
  );
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}
