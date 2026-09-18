import { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router';

import { useCheckinMutation } from '../api/queries';
import { CheckinOutcomeView } from '../components/CheckinOutcomeView';
import { Page } from '../components/Page';
import { LoadingState } from '../components/states';
import { useI18n } from '../i18n/i18n';
import { useCheckinPayload } from '../app/checkinPayload';
import { outcomeFromError, outcomeFromResult } from '../lib/checkinOutcome';
import { InvalidCheckinLink } from './InvalidCheckinLink';

/** `/checkin/qr` — reached from a scanned QR or a `ci_…` start_param. */
export function AutoCheckinScreen() {
  const { t } = useI18n();
  const { payload } = useCheckinPayload();

  return (
    <Page title={t('checkin.title')} backTo="/">
      {payload ? (
        <AutoCheckin key={`${payload.eventId}_${payload.code}`} {...payload} />
      ) : (
        <InvalidCheckinLink />
      )}
    </Page>
  );
}

function AutoCheckin({ eventId, code }: { eventId: number; code: string }) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { mutate, status, data, error } = useCheckinMutation();
  const sentRef = useRef(false);

  const send = () => mutate({ event_id: eventId, code, method: 'qr' });

  useEffect(() => {
    // Exactly one automatic attempt per payload (StrictMode re-runs effects in development).
    if (sentRef.current) return;
    sentRef.current = true;
    mutate({ event_id: eventId, code, method: 'qr' });
  }, [mutate, eventId, code]);

  if (status === 'success') {
    return (
      <CheckinOutcomeView
        outcome={outcomeFromResult(data)}
        eventId={eventId}
        onTryAgain={() => void navigate(`/checkin?event=${eventId}`)}
      />
    );
  }
  if (status === 'error') {
    return (
      <CheckinOutcomeView
        outcome={outcomeFromError(error)}
        eventId={eventId}
        onRetry={send}
        onTryAgain={() => void navigate(`/checkin?event=${eventId}`)}
      />
    );
  }
  return <LoadingState label={t('checkin.sending')} />;
}
