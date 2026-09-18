import { Button, Flex, Typography } from '@maxhub/max-ui';
import { useState } from 'react';
import { useParams } from 'react-router';

import { useApi } from '../api/context';
import { useAttendanceQuery } from '../api/queries';
import type { Attendance, CheckinMethod } from '../api/types';
import { useSession } from '../app/session';
import { NotFoundState } from '../components/NotFound';
import { Page } from '../components/Page';
import { EmptyState, InlineError, QueryState } from '../components/states';
import { useI18n } from '../i18n/i18n';
import { formatDateTime } from '../lib/datetime';
import { attendanceFileName, saveBlob } from '../lib/download';
import { parseId } from '../lib/routeParams';

export function OrgAttendanceScreen() {
  const { t } = useI18n();
  const eventId = parseId(useParams().id);

  return (
    <Page
      title={t('org.attendanceTitle')}
      backTo={eventId === null ? '/org' : `/org/events/${eventId}`}
    >
      {eventId === null ? <NotFoundState /> : <AttendanceLoader eventId={eventId} />}
    </Page>
  );
}

function AttendanceLoader({ eventId }: { eventId: number }) {
  const query = useAttendanceQuery(eventId);
  return (
    <QueryState query={query}>
      {(attendance) => <AttendanceView eventId={eventId} attendance={attendance} />}
    </QueryState>
  );
}

function AttendanceView({ eventId, attendance }: { eventId: number; attendance: Attendance }) {
  const { t, lang } = useI18n();
  const me = useSession();

  return (
    <Flex direction="column" gap={16}>
      <Flex direction="column" gap={4}>
        <Typography.Body variant="medium">
          {t('org.checkinCount', { count: attendance.checkin_count })}
        </Typography.Body>
        <Typography.Body variant="medium" className="muted">
          {t('org.rsvpCount', { count: attendance.rsvp_count })}
        </Typography.Body>
      </Flex>

      <CsvDownload eventId={eventId} />

      {attendance.items.length === 0 ? (
        <EmptyState title={t('org.attendanceEmpty')} />
      ) : (
        <div className="table-scroll">
          <table className="attendance">
            <thead>
              <tr>
                <th scope="col">{t('org.colName')}</th>
                <th scope="col">{t('org.colMethod')}</th>
                <th scope="col">{t('org.colTime')}</th>
              </tr>
            </thead>
            <tbody>
              {attendance.items.map((item) => (
                <tr key={`${item.user_id}-${item.checked_in_at}`}>
                  <td>{item.first_name}</td>
                  <td>{methodLabel(item.method, t)}</td>
                  <td>{formatDateTime(item.checked_in_at, me.university.timezone, lang)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Flex>
  );
}

function methodLabel(method: CheckinMethod, t: ReturnType<typeof useI18n>['t']): string {
  return method === 'qr' ? t('org.methodQr') : t('org.methodCode');
}

type CsvState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ready'; text: string; saved: boolean }
  | { status: 'error'; error: unknown };

/** Downloads `attendance.csv` (§7) with the auth header, then saves it or shows it inline. */
function CsvDownload({ eventId }: { eventId: number }) {
  const { t } = useI18n();
  const api = useApi();
  const [state, setState] = useState<CsvState>({ status: 'idle' });
  const [showText, setShowText] = useState(false);

  const download = async () => {
    setState({ status: 'loading' });
    try {
      const blob = await api.getAttendanceCsv(eventId);
      const text = await blob.text();
      const outcome = saveBlob(blob, attendanceFileName(eventId));
      setState({ status: 'ready', text, saved: outcome === 'saved' });
      if (outcome !== 'saved') setShowText(true);
    } catch (error) {
      setState({ status: 'error', error });
    }
  };

  const loading = state.status === 'loading';

  return (
    <Flex direction="column" gap={8}>
      <Button
        size="large"
        variant="secondary"
        stretched
        loading={loading}
        disabled={loading}
        onClick={() => void download()}
      >
        {t('org.downloadCsv')}
      </Button>

      {state.status === 'error' && <InlineError error={state.error} />}

      {state.status === 'ready' && (
        <>
          <Button size="small" variant="ghost" onClick={() => setShowText((value) => !value)}>
            {showText ? t('org.csvHide') : t('org.csvShow')}
          </Button>
          {showText && (
            <>
              <Typography.Body variant="small" className="muted">
                {t('org.csvCopyHint')}
              </Typography.Body>
              <textarea
                className="field__control"
                readOnly
                rows={8}
                aria-label={t('org.downloadCsv')}
                value={state.text}
              />
            </>
          )}
        </>
      )}
    </Flex>
  );
}
