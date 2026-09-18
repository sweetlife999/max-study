import { Button, Flex, Spinner, Typography } from '@maxhub/max-ui';
import type { UseQueryResult } from '@tanstack/react-query';
import type { ReactNode } from 'react';

import { useI18n } from '../i18n/i18n';
import { describeError } from '../lib/errorMessage';

export function LoadingState({ label }: { label?: string }) {
  const { t } = useI18n();
  return (
    <Flex className="state" direction="column" align="center" justify="center" gap={12}>
      <Spinner size={32} appearance="themed" aria-hidden />
      <Typography.Body variant="medium" role="status">
        {label ?? t('common.loading')}
      </Typography.Body>
    </Flex>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { t } = useI18n();
  return (
    <Flex className="state" direction="column" align="center" justify="center" gap={12}>
      <Typography.Title variant="small-strong">{t('error.title')}</Typography.Title>
      <Typography.Body variant="medium" role="alert" className="state__text">
        {describeError(error, t)}
      </Typography.Body>
      {onRetry && (
        <Button variant="secondary" size="medium" onClick={onRetry}>
          {t('common.retry')}
        </Button>
      )}
    </Flex>
  );
}

export function EmptyState({
  title,
  text,
  action,
}: {
  title: string;
  text?: string;
  action?: ReactNode;
}) {
  return (
    <Flex className="state" direction="column" align="center" justify="center" gap={8}>
      <Typography.Title variant="small-strong">{title}</Typography.Title>
      {text && (
        <Typography.Body variant="medium" className="state__text">
          {text}
        </Typography.Body>
      )}
      {action}
    </Flex>
  );
}

/** Renders loading / error-with-retry for a query and hands loaded data to `children`. */
export function QueryState<T>({
  query,
  children,
}: {
  query: UseQueryResult<T>;
  children: (data: T) => ReactNode;
}) {
  if (query.isPending) return <LoadingState />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  return <>{children(query.data)}</>;
}

/** Inline, non-blocking error text for a failed action (mutation). */
export function InlineError({ error }: { error: unknown }) {
  const { t } = useI18n();
  if (!error) return null;
  return (
    <Typography.Body variant="small" role="alert" className="inline-error">
      {describeError(error, t)}
    </Typography.Body>
  );
}
