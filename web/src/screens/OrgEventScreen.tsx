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

  return (
    <Flex direction="column" gap={16}>
      <EventInfo event={event} />

      <section className="panel">
        <Flex direction="column" gap={8}>
          <Flex align="center" justify="space-between" gap={12}>
            <Typography.Label variant="medium-strong" asChild>
              <label htmlFor="checkin-open">{t('org.checkinOpen')}</label>
            </Typography.Label>
            <Switch
              id="checkin-open"
              checked={event.checkin_open}
              disabled={update.isPending}
              onChange={(changeEvent) => {
                update.mutate({ checkin_open: changeEvent.target.checked });
              }}
            />
          </Flex>
          <Typography.Body variant="small" className="muted">
            {t('org.checkinOpenHint')}
          </Typography.Body>
          <InlineError error={update.error} />
        </Flex>
      </section>

      <Flex direction="column" gap={8}>
        <Button
          size="large"
          stretched
          disabled={!event.checkin_open}
          onClick={() => void navigate(`/org/events/${event.id}/qr`)}
        >
          {t('org.showQr')}
        </Button>
        {!event.checkin_open && (
          <Typography.Body variant="small" className="muted">
            {t('org.qrClosedHint')}
          </Typography.Body>
        )}
      </Flex>

      <CellList mode="island">
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
