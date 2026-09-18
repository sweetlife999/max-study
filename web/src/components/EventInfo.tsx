import { CellList, CellSimple, Flex, Typography } from '@maxhub/max-ui';

import type { Event } from '../api/types';
import { useSession } from '../app/session';
import { useI18n } from '../i18n/i18n';
import { formatRange } from '../lib/datetime';

/** Read-only event facts shared by the student and organizer screens. */
export function EventInfo({ event }: { event: Event }) {
  const { t, lang } = useI18n();
  const me = useSession();

  return (
    <Flex direction="column" gap={12}>
      <Flex direction="column" gap={4}>
        <Typography.Label variant="medium" className="muted">
          {event.kind_title}
        </Typography.Label>
        <Typography.Headline variant="small" asChild>
          <h2 className="event-title">{event.title}</h2>
        </Typography.Headline>
      </Flex>
      <CellList mode="island">
        <CellSimple
          overline={t('event.when')}
          title={formatRange(event.starts_at, event.ends_at, me.university.timezone, lang)}
        />
        {event.location && <CellSimple overline={t('event.where')} title={event.location} />}
        <CellSimple overline={t('event.points')} title={String(event.points)} />
        <CellSimple overline={t('event.attendees')} title={String(event.attendees_count)} />
      </CellList>
      {event.description && (
        <Typography.Body variant="medium" className="event-description">
          {event.description}
        </Typography.Body>
      )}
    </Flex>
  );
}
