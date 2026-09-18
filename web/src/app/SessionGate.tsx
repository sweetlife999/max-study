import { Flex, Typography } from '@maxhub/max-ui';
import { useEffect, type ReactNode } from 'react';

import { isApiError } from '../api/errors';
import { useMeQuery } from '../api/queries';
import { useBridge } from '../bridge/context';
import { ErrorState, LoadingState } from '../components/states';
import { I18nProvider } from '../i18n/I18nProvider';
import { resolveLang, useI18n } from '../i18n/i18n';
import { ConsentScreen } from '../screens/ConsentScreen';
import { SessionProvider } from './SessionProvider';

/**
 * Loads `GET /api/me`, picks the interface language and keeps the consent screen (§7) in front
 * of everything else. Every branch has a way forward: retry, consent, or an explanation.
 */
export function SessionGate({ children }: { children: ReactNode }) {
  const bridge = useBridge();
  const query = useMeQuery();

  useEffect(() => {
    bridge.ready();
  }, [bridge]);

  return (
    <I18nProvider lang={query.data?.lang ?? resolveLang(bridge.languageCode)}>
      <GateBody query={query}>{children}</GateBody>
    </I18nProvider>
  );
}

function GateBody({
  query,
  children,
}: {
  query: ReturnType<typeof useMeQuery>;
  children: ReactNode;
}) {
  if (query.isPending) return <LoadingState />;
  if (query.isError) {
    return <SessionError error={query.error} onRetry={() => void query.refetch()} />;
  }
  if (!query.data.consent) return <ConsentScreen me={query.data} />;
  return <SessionProvider me={query.data}>{children}</SessionProvider>;
}

function SessionError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const { t } = useI18n();
  const bridge = useBridge();
  const looksOutsideMax = !bridge.isInMax || (isApiError(error) && error.isUnauthorized);

  if (!looksOutsideMax) return <ErrorState error={error} onRetry={onRetry} />;

  return (
    <Flex className="state" direction="column" align="center" justify="center" gap={12}>
      <Typography.Title variant="small-strong">{t('gate.outsideMaxTitle')}</Typography.Title>
      <Typography.Body variant="medium" className="state__text" role="status">
        {t('gate.outsideMaxText')}
      </Typography.Body>
    </Flex>
  );
}
