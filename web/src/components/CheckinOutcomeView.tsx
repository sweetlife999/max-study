import { Button, Flex, Typography } from '@maxhub/max-ui';
import { useEffect, type ReactNode } from 'react';
import { useNavigate } from 'react-router';

import { useBridge } from '../bridge/context';
import { useI18n } from '../i18n/i18n';
import type { CheckinOutcome } from '../lib/checkinOutcome';
import { describeError } from '../lib/errorMessage';

interface CheckinOutcomeViewProps {
  outcome: CheckinOutcome;
  eventId: number | null;
  /** Repeats the same request (only offered for unexpected failures). */
  onRetry?: () => void;
  /** Lets the user scan or type a new code. */
  onTryAgain: () => void;
}

export function CheckinOutcomeView({
  outcome,
  eventId,
  onRetry,
  onTryAgain,
}: CheckinOutcomeViewProps) {
  const { t } = useI18n();
  const bridge = useBridge();
  const navigate = useNavigate();

  const succeeded = outcome.kind !== 'failure';
  useEffect(() => {
    bridge.notify(succeeded ? 'success' : 'error');
  }, [bridge, succeeded]);

  const toHome = (
    <Button size="large" variant="secondary" stretched onClick={() => void navigate('/')}>
      {t('common.toHome')}
    </Button>
  );
  const toEvent = (id: number | null) =>
    id === null ? null : (
      <Button size="large" stretched onClick={() => void navigate(`/events/${id}`)}>
        {t('checkin.toEvent')}
      </Button>
    );

  if (outcome.kind !== 'failure') {
    const { result } = outcome;
    return (
      <Result
        tone="success"
        title={outcome.kind === 'success' ? t('checkin.successTitle') : t('checkin.alreadyTitle')}
        lines={[
          result.event.title,
          outcome.kind === 'success'
            ? t('checkin.pointsEarned', { points: result.event.points, total: result.points_total })
            : t('checkin.pointsTotal', { total: result.points_total }),
          result.completed_step
            ? t('checkin.stepCompleted', { title: result.completed_step.title })
            : null,
        ]}
      >
        {toEvent(result.event.id)}
        {toHome}
      </Result>
    );
  }

  switch (outcome.failure) {
    case 'code_invalid':
      return (
        <Result
          tone="error"
          title={t('checkin.codeInvalidTitle')}
          lines={[t('checkin.codeInvalidText')]}
        >
          <Button size="large" stretched onClick={onTryAgain}>
            {t('checkin.tryAgain')}
          </Button>
          {toHome}
        </Result>
      );
    case 'closed':
      return (
        <Result tone="error" title={t('checkin.closedTitle')} lines={[t('checkin.closedText')]}>
          {toEvent(eventId)}
          {toHome}
        </Result>
      );
    case 'rate_limited':
      return (
        <Result
          tone="error"
          title={t('checkin.rateLimitedTitle')}
          lines={[describeError(outcome.error, t)]}
        >
          {toHome}
        </Result>
      );
    case 'not_found':
      return (
        <Result tone="error" title={t('checkin.notFoundTitle')} lines={[t('checkin.notFoundText')]}>
          <Button size="large" stretched onClick={onTryAgain}>
            {t('checkin.tryAgain')}
          </Button>
          {toHome}
        </Result>
      );
    case 'other':
      return (
        <Result tone="error" title={t('error.title')} lines={[describeError(outcome.error, t)]}>
          {onRetry && (
            <Button size="large" stretched onClick={onRetry}>
              {t('common.retry')}
            </Button>
          )}
          {toHome}
        </Result>
      );
  }
}

function Result({
  tone,
  title,
  lines,
  children,
}: {
  tone: 'success' | 'error';
  title: string;
  lines: (string | null)[];
  children: ReactNode;
}) {
  return (
    <Flex direction="column" gap={16} className={`result result_${tone}`}>
      <div className="result__icon" aria-hidden>
        {tone === 'success' ? '✓' : '!'}
      </div>
      <Typography.Headline variant="small" asChild>
        <h2 role={tone === 'error' ? 'alert' : 'status'}>{title}</h2>
      </Typography.Headline>
      {lines
        .filter((line): line is string => Boolean(line))
        .map((line) => (
          <Typography.Body key={line} variant="medium">
            {line}
          </Typography.Body>
        ))}
      <Flex direction="column" gap={8}>
        {children}
      </Flex>
    </Flex>
  );
}
