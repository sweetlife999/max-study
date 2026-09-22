import { Button, Flex, Typography } from '@maxhub/max-ui';
import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';

import { useCheckinPayload } from '../app/checkinPayload';
import { useSession } from '../app/session';
import { useCheckinMutation, useEventsQuery } from '../api/queries';
import type { Event } from '../api/types';
import { useBridge } from '../bridge/context';
import { CheckinOutcomeView } from '../components/CheckinOutcomeView';
import { Page } from '../components/Page';
import { EmptyState, ErrorState, LoadingState } from '../components/states';
import { useI18n } from '../i18n/i18n';
import { extractCheckinFromScan, normalizeManualCode } from '../lib/checkinPayload';
import { outcomeFromError, outcomeFromResult } from '../lib/checkinOutcome';
import { parseId } from '../lib/routeParams';

type ScanNotice = 'unavailable' | 'not_checkin_qr' | null;

export function CheckinScreen() {
  const { t } = useI18n();
  const bridge = useBridge();
  const navigate = useNavigate();
  const me = useSession();
  const { setPayload } = useCheckinPayload();
  const [searchParams] = useSearchParams();
  const preselectedId = parseId(searchParams.get('event'));
  const needsFreshScan = searchParams.get('fresh') === '1';

  const [scanning, setScanning] = useState(false);
  const [scanNotice, setScanNotice] = useState<ScanNotice>(null);
  // Incremented whenever a scan result must be ignored (new scan, manual submit, unmount).
  const scanTokenRef = useRef(0);
  useEffect(
    () => () => {
      scanTokenRef.current += 1;
    },
    [],
  );

  const scan = async () => {
    scanTokenRef.current += 1;
    const token = scanTokenRef.current;
    setScanNotice(null);
    setScanning(true);
    const outcome = await bridge.scanQr();
    if (token !== scanTokenRef.current) return;
    setScanning(false);
    if (outcome.status === 'unavailable') {
      setScanNotice('unavailable');
      return;
    }
    if (outcome.status === 'cancelled') return;
    const payload = extractCheckinFromScan(outcome.text);
    if (!payload) {
      setScanNotice('not_checkin_qr');
      return;
    }
    setPayload(payload);
    void navigate('/checkin/qr');
  };

  const cancelPendingScan = () => {
    scanTokenRef.current += 1;
    setScanning(false);
  };

  return (
    <Page title={t('checkin.title')} backTo="/">
      <Flex direction="column" gap={20}>
        {needsFreshScan && (
          <Typography.Body variant="medium" role="status">
            {t('checkin.freshAfterConsent')}
          </Typography.Body>
        )}
        <section className="panel" aria-labelledby="scan-heading">
          <Flex direction="column" gap={8} className="full-width">
            <Typography.Title variant="small-strong" asChild>
              <h2 id="scan-heading">{t('checkin.scanHeading')}</h2>
            </Typography.Title>
            {bridge.canScanQr ? (
              <Button
                size="large"
                stretched
                loading={scanning}
                disabled={scanning}
                onClick={() => void scan()}
              >
                {t('checkin.scan')}
              </Button>
            ) : (
              <Typography.Body variant="medium" className="muted">
                {t('checkin.scannerUnavailable')}
              </Typography.Body>
            )}
            {scanNotice === 'unavailable' && (
              <Typography.Body variant="medium" role="alert">
                {t('checkin.scannerFailed')}
              </Typography.Body>
            )}
            {scanNotice === 'not_checkin_qr' && (
              <Typography.Body variant="medium" role="alert">
                {t('checkin.notCheckinQr')}
              </Typography.Body>
            )}
          </Flex>
        </section>

        <ManualCheckin
          isOrganizer={me.is_organizer}
          preselectedId={preselectedId}
          onStart={cancelPendingScan}
          onOpenOrganizerTools={() => void navigate('/org')}
        />
      </Flex>
    </Page>
  );
}

function useOpenEvents() {
  const upcoming = useEventsQuery('upcoming');
  const past = useEventsQuery('past');
  const queries = [upcoming, past];
  const failed = queries.find((q) => q.isError);
  const events: Event[] = [...(upcoming.data?.items ?? []), ...(past.data?.items ?? [])].filter(
    (event, index, all) =>
      event.checkin_open && all.findIndex((other) => other.id === event.id) === index,
  );
  return {
    isPending: queries.some((q) => q.isPending),
    error: failed?.error ?? null,
    refetch: () => queries.forEach((q) => void q.refetch()),
    events,
  };
}

function ManualCheckin({
  isOrganizer,
  preselectedId,
  onStart,
  onOpenOrganizerTools,
}: {
  isOrganizer: boolean;
  preselectedId: number | null;
  onStart: () => void;
  onOpenOrganizerTools: () => void;
}) {
  const { t } = useI18n();
  const open = useOpenEvents();
  const checkin = useCheckinMutation();
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [code, setCode] = useState('');
  const [codeError, setCodeError] = useState(false);
  const [eventError, setEventError] = useState(false);

  const effectiveId =
    selectedId ??
    (open.events.some((e) => e.id === preselectedId) ? preselectedId : null) ??
    (open.events.length === 1 ? (open.events[0]?.id ?? null) : null);

  if (checkin.status === 'success' || checkin.status === 'error') {
    const outcome =
      checkin.status === 'success'
        ? outcomeFromResult(checkin.data)
        : outcomeFromError(checkin.error);
    return (
      <CheckinOutcomeView
        outcome={outcome}
        eventId={checkin.variables.event_id}
        onRetry={() => checkin.mutate(checkin.variables)}
        onTryAgain={() => {
          checkin.reset();
          setCode('');
        }}
      />
    );
  }

  const submit = () => {
    const normalized = normalizeManualCode(code);
    setCodeError(normalized === null);
    setEventError(effectiveId === null);
    if (normalized === null || effectiveId === null) return;
    onStart();
    checkin.mutate({ event_id: effectiveId, code: normalized, method: 'code' });
  };

  let body;
  if (open.isPending) {
    body = <LoadingState />;
  } else if (open.error) {
    body = <ErrorState error={open.error} onRetry={open.refetch} />;
  } else if (open.events.length === 0) {
    body = (
      <EmptyState
        title={t('checkin.noOpenEvents')}
        text={t('checkin.noOpenEventsHint')}
        action={
          <Button variant="secondary" onClick={open.refetch}>
            {t('common.refresh')}
          </Button>
        }
      />
    );
  } else {
    body = (
      <form
        noValidate
        onSubmit={(formEvent) => {
          formEvent.preventDefault();
          submit();
        }}
      >
        <Flex direction="column" gap={12}>
          <label className="field">
            <span className="field__label">{t('checkin.eventLabel')}</span>
            <select
              className="field__control"
              value={effectiveId ?? ''}
              aria-invalid={eventError}
              onChange={(changeEvent) => {
                setSelectedId(parseId(changeEvent.target.value));
                setEventError(false);
              }}
            >
              <option value="" disabled>
                {t('checkin.eventPlaceholder')}
              </option>
              {open.events.map((event) => (
                <option key={event.id} value={event.id}>
                  {event.title}
                </option>
              ))}
            </select>
            {eventError && (
              <span className="field__error" role="alert">
                {t('checkin.eventRequired')}
              </span>
            )}
          </label>
          <label className="field">
            <span className="field__label">{t('checkin.codeLabel')}</span>
            <input
              className="field__control field__control_code"
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              pattern="[0-9]{6}"
              placeholder="000000"
              value={code}
              aria-invalid={codeError}
              onChange={(changeEvent) => {
                setCode(changeEvent.target.value.replace(/[^0-9]/g, '').slice(0, 6));
                setCodeError(false);
              }}
            />
            {codeError && (
              <span className="field__error" role="alert">
                {t('checkin.codeInvalidFormat')}
              </span>
            )}
          </label>
          <Button
            type="submit"
            size="large"
            stretched
            loading={checkin.isPending}
            disabled={checkin.isPending}
          >
            {t('checkin.submit')}
          </Button>
        </Flex>
      </form>
    );
  }

  return (
    <section className="panel" aria-labelledby="manual-heading">
      <Flex direction="column" gap={8} className="full-width">
        <Typography.Title variant="small-strong" asChild>
          <h2 id="manual-heading">{t('checkin.manualHeading')}</h2>
        </Typography.Title>
        <Typography.Body variant="small" className="muted">
          {isOrganizer ? t('checkin.organizerHint') : t('checkin.attendeeHint')}
        </Typography.Body>
        {isOrganizer && (
          <Button size="large" variant="secondary" stretched onClick={onOpenOrganizerTools}>
            {t('checkin.organizerAction')}
          </Button>
        )}
        {body}
      </Flex>
    </section>
  );
}
