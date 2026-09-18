import { Button, Flex, Typography } from '@maxhub/max-ui';
import { useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { useNavigate, useParams } from 'react-router';

import { isApiError } from '../api/errors';
import {
  queryKeys,
  useAttendanceQuery,
  useEventQuery,
  useSendQrToChatMutation,
} from '../api/queries';
import { useRotatingQr } from '../api/useRotatingQr';
import type { Event } from '../api/types';
import { useBridge, useNativeBackButton } from '../bridge/context';
import { NotFoundState } from '../components/NotFound';
import { Page } from '../components/Page';
import { QrCode } from '../components/QrCode';
import { ErrorState, InlineError, LoadingState, QueryState } from '../components/states';
import { useI18n } from '../i18n/i18n';
import { secondsLeft } from '../lib/qrTiming';
import { parseId } from '../lib/routeParams';
import { useNow } from '../lib/useNow';

const COUNTDOWN_TICK_MS = 250;

export function OrgQrScreen() {
  const { t } = useI18n();
  const eventId = parseId(useParams().id);
  if (eventId === null) {
    return (
      <Page title={t('org.qrTitle')} backTo="/org">
        <NotFoundState />
      </Page>
    );
  }
  return <QrLoader eventId={eventId} />;
}

function QrLoader({ eventId }: { eventId: number }) {
  const query = useEventQuery(eventId);
  return <QueryState query={query}>{(event) => <QrScreenBody event={event} />}</QueryState>;
}

/**
 * Full-screen rotating QR (§9). The screen leaves the app chrome behind on purpose: the phone is
 * handed around or propped up, so the code and the digits must be as large as possible.
 */
function QrScreenBody({ event }: { event: Event }) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const bridge = useBridge();
  const back = `/org/events/${event.id}`;

  useNativeBackButton(() => void navigate(back));

  const rotating = useRotatingQr(event.id, event.checkin_open);
  const attendance = useAttendanceQuery(event.id, { poll: event.checkin_open });
  const sendToChat = useSendQrToChatMutation(event.id);
  const now = useNow(COUNTDOWN_TICK_MS, event.checkin_open);
  const queryClient = useQueryClient();

  // §7: the QR endpoint answers 403 as soon as `checkin_open` is false. Our cached event then
  // says otherwise, so reload it — the screen must explain that check-in is closed instead of
  // leaving a stale code and a generic warning on display.
  const forbidden = isApiError(rotating.error) && rotating.error.status === 403;
  useEffect(() => {
    if (!forbidden) return;
    void queryClient.invalidateQueries({ queryKey: queryKeys.event(event.id) });
  }, [forbidden, queryClient, event.id]);

  useEffect(() => {
    if (!event.checkin_open) return undefined;
    bridge.requestMaxBrightness();
    return () => bridge.restoreBrightness();
  }, [bridge, event.checkin_open]);

  if (!event.checkin_open) {
    return (
      <main className="page">
        <Flex direction="column" gap={16} align="center" className="state">
          <Typography.Title variant="small-strong">{t('org.qrClosed')}</Typography.Title>
          <Typography.Body variant="medium" className="state__text">
            {t('org.qrClosedHint')}
          </Typography.Body>
          <Button size="large" onClick={() => void navigate(back)}>
            {t('org.qrOpenCheckin')}
          </Button>
        </Flex>
      </main>
    );
  }

  if (rotating.isLoading) return <LoadingState />;
  if (!rotating.qr || !rotating.timing) {
    return <ErrorState error={rotating.error} onRetry={rotating.refresh} />;
  }

  const remaining = secondsLeft(rotating.timing, now);
  const checkedIn = attendance.data?.checkin_count ?? 0;

  return (
    <main className="qr-screen">
      <QrCode
        className="qr-screen__canvas"
        value={rotating.qr.deeplink}
        label={t('org.qrTitle')}
        fallback={
          <Typography.Body variant="medium" role="alert">
            {t('error.generic')}
          </Typography.Body>
        }
      />

      <div className="qr-screen__code" aria-label={t('org.qrCodeLabel')}>
        {rotating.qr.code}
      </div>

      <Flex className="qr-screen__meta" role="status">
        <span>{t('org.qrExpiresIn', { seconds: remaining })}</span>
        <span>{t('org.qrCheckedIn', { count: checkedIn })}</span>
      </Flex>

      <Typography.Body variant="small">{t('org.qrHint')}</Typography.Body>

      {rotating.error !== null && <ErrorNotice onRetry={rotating.refresh} />}

      <Flex className="qr-screen__actions" direction="column" gap={8}>
        <Button
          size="large"
          variant="secondary"
          stretched
          loading={sendToChat.isPending}
          disabled={sendToChat.isPending}
          onClick={() => sendToChat.mutate()}
        >
          {t('org.qrSendToChat')}
        </Button>
        {sendToChat.isSuccess && (
          <Typography.Body variant="small" role="status">
            {t('org.qrSentToChat')}
          </Typography.Body>
        )}
        <InlineError error={sendToChat.error} />
        <Button size="large" variant="ghost" stretched onClick={() => void navigate(back)}>
          {t('common.back')}
        </Button>
      </Flex>
    </main>
  );
}

function ErrorNotice({ onRetry }: { onRetry: () => void }) {
  const { t } = useI18n();
  return (
    <Flex direction="column" align="center" gap={8}>
      <Typography.Body variant="small" role="alert" className="inline-error">
        {t('org.qrStale')}
      </Typography.Body>
      <Button size="small" variant="secondary" onClick={onRetry}>
        {t('common.retry')}
      </Button>
    </Flex>
  );
}
