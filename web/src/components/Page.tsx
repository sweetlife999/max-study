import { Button, Flex, Typography } from '@maxhub/max-ui';
import type { ReactNode } from 'react';
import { useNavigate } from 'react-router';

import { useNativeBackButton } from '../bridge/context';
import { useI18n } from '../i18n/i18n';

interface PageProps {
  title: string;
  /** Parent route. Explicit paths keep "back" working when the app was opened by a deeplink. */
  backTo?: string;
  children: ReactNode;
  className?: string;
}

export function Page({ title, backTo, children, className }: PageProps) {
  const navigate = useNavigate();
  const { t } = useI18n();
  const goBack = backTo === undefined ? null : () => void navigate(backTo);
  useNativeBackButton(goBack);

  return (
    <main className={className ? `page ${className}` : 'page'}>
      <Flex className="page__header" align="center" gap={8}>
        {goBack && (
          <Button variant="ghost" size="small" onClick={goBack} aria-label={t('common.back')}>
            ←
          </Button>
        )}
        <Typography.Headline variant="medium" asChild>
          <h1 className="page__title">{title}</h1>
        </Typography.Headline>
      </Flex>
      {children}
    </main>
  );
}
