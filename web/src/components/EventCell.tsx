import { CellSimple } from '@maxhub/max-ui';
import { useNavigate } from 'react-router';

import type { Event } from '../api/types';
import { useSession } from '../app/session';
import { useI18n } from '../i18n/i18n';
import { formatRange } from '../lib/datetime';

export function EventCell({ event, to }: { event: Event; to: string }) {
  const navigate = useNavigate();
  const { t, lang } = useI18n();
  const me = useSession();

  const badges = [
    event.checked_in ? t('event.badgeCheckedIn') : null,
    !event.checked_in && event.rsvp ? t('event.badgeGoing') : null,
    event.checkin_open ? t('event.badgeCheckinOpen') : null,
  ].filter((badge): badge is string => badge !== null);

  return (
    <CellSimple
      as="button"
      showChevron
      overline={event.kind_title}
      title={event.title}
      subtitle={[
        formatRange(event.starts_at, event.ends_at, me.university.timezone, lang),
        ...badges,
      ]
        .filter(Boolean)
        .join(' · ')}
      onClick={() => void navigate(to)}
    />
  );
}
