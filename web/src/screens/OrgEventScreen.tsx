import { Button, CellList, CellSimple, Flex, Switch, Typography } from '@maxhub/max-ui';
import { useNavigate, useParams } from 'react-router';

import { useEventQuery, useUpdateEventMutation } from '../api/queries';
import type { Event } from '../api/types';
import { EventInfo } from '../components/EventInfo';
import { NotFoundState } from '../components/NotFound';
import { Page } from '../components/Page';
import { InlineError, QueryState } from '../components/states';
import { useI18n } from '../i18n/i18n';
import { parseId } from '../lib/routeParams';
import { useNow } from '../lib/useNow';

export function OrgEventScreen() {
  const { t } = useI18n();
  const eventId = parseId(useParams().id);

  return (
    <Page title={t('org.manageTitle')} backTo="/org">
      {eventId === null ? <NotFoundState /> : <OrgEventLoader eventId={eventId} />}
    </Page>
  );
}

function OrgEventLoader({ eventId }: { eventId: number }) {
  const query = useEventQuery(eventId);
  return <QueryState query={query}>{(event) => <OrgEventDetails event={event} />}</QueryState>;
}

function OrgEventDetails({ event }: { event: Event }) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const update = useUpdateEventMutation(event.id);
  const now = useNow(1000);
  const isFinished = Date.parse(event.ends_at) <= now;

  return (
    <Flex direction="column" gap={16} align="stretch" className="org-event-screen">
      <EventInfo event={event} />

      <section className="panel full-width" aria-labelledby="checkin-open-heading">
        <Flex direction="column" gap={8} align="stretch" className="full-width">
          <label className="checkin-toggle">
            <Typography.Label variant="medium-strong" asChild>
              <span id="checkin-open-heading">{t('org.checkinOpen')}</span>
            </Typography.Label>
            <Switch
              aria-label={t('org.checkinOpen')}
              checked={event.checkin_open && !isFinished}
              disabled={update.isPending || isFinished}
              onChange={(changeEvent) => {
                update.mutate({ checkin_open: changeEvent.target.checked });
              }}
            />
          </label>
          <Typography.Body variant="small" className="muted">
            {isFinished ? t('org.eventFinishedHint') : t('org.checkinOpenHint')}
          </Typography.Body>
          <InlineError error={update.error} />
        </Flex>
      </section>

      <Flex direction="column" gap={8} align="stretch" className="full-width org-event-screen__qr">
        <Button
          size="large"
          stretched
          disabled={!event.checkin_open || isFinished}
          onClick={() => void navigate(`/org/events/${event.id}/qr`)}
        >
          {t('org.showQr')}
        </Button>
        {!isFinished && !event.checkin_open ? (
          <Typography.Body variant="small" className="muted">
            {t('org.qrClosedHint')}
          </Typography.Body>
        ) : null}
      </Flex>

      <CellList mode="island" className="full-width">
        <CellSimple
          as="button"
          showChevron
          title={t('org.attendance')}
          onClick={() => void navigate(`/org/events/${event.id}/attendance`)}
        />
        <CellSimple
          as="button"
          showChevron
          title={t('org.edit')}
          onClick={() => void navigate(`/org/events/${event.id}/edit`)}
        />
      </CellList>
    </Flex>
  );
}
