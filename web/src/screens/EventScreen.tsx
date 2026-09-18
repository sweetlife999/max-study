import { Button, Flex, Typography } from '@maxhub/max-ui';
import { useState } from 'react';
import { useNavigate, useParams } from 'react-router';

import { useEventQuery, useRsvpMutation } from '../api/queries';
import type { Event } from '../api/types';
import { EventInfo } from '../components/EventInfo';
import { NotFoundState } from '../components/NotFound';
import { Page } from '../components/Page';
import { InlineError, QueryState } from '../components/states';
import { useI18n } from '../i18n/i18n';
import { parseId } from '../lib/routeParams';

export function EventScreen() {
  const { t } = useI18n();
  const eventId = parseId(useParams().id);

  return (
    <Page title={t('event.title')} backTo="/events">
      {eventId === null ? <NotFoundState /> : <EventLoader eventId={eventId} />}
    </Page>
  );
}

function EventLoader({ eventId }: { eventId: number }) {
  const query = useEventQuery(eventId);
  return <QueryState query={query}>{(event) => <EventDetails event={event} />}</QueryState>;
}

function EventDetails({ event }: { event: Event }) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const rsvp = useRsvpMutation(event.id);
  const [openedAt] = useState(() => Date.now());
  const isOver = Date.parse(event.ends_at) < openedAt;

  return (
    <Flex direction="column" gap={16}>
      <EventInfo event={event} />

      <section className="panel" aria-label={t('event.checkinSection')}>
        {event.checked_in ? (
          <Typography.Body variant="medium-strong" role="status">
            ✓ {t('event.checkedIn')}
          </Typography.Body>
        ) : event.checkin_open ? (
          <Button size="large" stretched onClick={() => void navigate(`/checkin?event=${event.id}`)}>
            {t('event.checkinNow')}
          </Button>
        ) : (
          <Typography.Body variant="medium" className="muted">
            {isOver ? t('event.checkinMissed') : t('event.checkinLater')}
          </Typography.Body>
        )}
      </section>

      {!isOver && (
        <section className="panel" aria-label={t('event.rsvpSection')}>
          <Flex direction="column" gap={8}>
            <Typography.Label variant="medium-strong">{t('event.rsvpQuestion')}</Typography.Label>
            <Flex gap={8}>
              <Button
                size="medium"
                variant={event.rsvp ? 'primary' : 'secondary'}
                aria-pressed={event.rsvp}
                loading={rsvp.isPending && rsvp.variables}
                disabled={rsvp.isPending}
                onClick={() => {
                  if (!event.rsvp) rsvp.mutate(true);
                }}
              >
                {t('event.going')}
              </Button>
              <Button
                size="medium"
                variant="secondary"
                aria-pressed={!event.rsvp}
                loading={rsvp.isPending && !rsvp.variables}
                disabled={rsvp.isPending}
                onClick={() => {
                  if (event.rsvp) rsvp.mutate(false);
                }}
              >
                {t('event.notGoing')}
              </Button>
            </Flex>
            {event.rsvp && (
              <Typography.Body variant="small" className="muted">
                {t('event.reminderNote')}
              </Typography.Body>
            )}
            <InlineError error={rsvp.error} />
          </Flex>
        </section>
      )}
    </Flex>
  );
}
