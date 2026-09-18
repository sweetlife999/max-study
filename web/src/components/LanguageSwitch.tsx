import { Button, Flex, Typography } from '@maxhub/max-ui';

import { useLangMutation } from '../api/queries';
import type { Lang } from '../api/types';
import { SUPPORTED_LANGS, useI18n } from '../i18n/i18n';
import { InlineError } from './states';

const LANG_LABELS: Record<Lang, string> = { ru: 'Русский', en: 'English' };

export function LanguageSwitch({ current }: { current: Lang }) {
  const { t } = useI18n();
  const mutation = useLangMutation();

  return (
    <Flex direction="column" gap={8}>
      <Typography.Label variant="medium-strong" id="language-label">
        {t('profile.language')}
      </Typography.Label>
      <Flex gap={8} role="group" aria-labelledby="language-label">
        {SUPPORTED_LANGS.map((lang) => (
          <Button
            key={lang}
            size="medium"
            variant={lang === current ? 'primary' : 'secondary'}
            aria-pressed={lang === current}
            disabled={mutation.isPending}
            onClick={() => {
              if (lang !== current) mutation.mutate(lang);
            }}
          >
            {LANG_LABELS[lang]}
          </Button>
        ))}
      </Flex>
      <InlineError error={mutation.error} />
    </Flex>
  );
}
