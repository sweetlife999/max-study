import { Button, CellList, Flex } from '@maxhub/max-ui';
import { useNavigate } from 'react-router';

import { useOrgEventsQuery } from '../api/queries';
import { useSession } from '../app/session';
import { EventCell } from '../components/EventCell';
import { NotFoundState } from '../components/NotFound';
import { Page } from '../components/Page';
import { EmptyState, QueryState } from '../components/states';
import { useI18n } from '../i18n/i18n';

export function OrgEventsScreen() {
  const { t } = useI18n();
  const me = useSession();
  const navigate = useNavigate();
  const query = useOrgEventsQuery();

  return (
    <Page title={t('org.eventsTitle')} backTo="/">
      {!me.is_organizer ? (
        <NotFoundState />
      ) : (
        <Flex direction="column" gap={16}>
          <Button size="large" stretched onClick={() => void navigate('/org/events/new')}>
            {t('org.create')}
          </Button>
          <QueryState query={query}>
            {({ items }) =>
              items.length === 0 ? (
                <EmptyState title={t('org.empty')} text={t('org.emptyHint')} />
              ) : (
                <CellList mode="island">
                  {items.map((event) => (
                    <EventCell key={event.id} event={event} to={`/org/events/${event.id}`} />
                  ))}
                </CellList>
              )
            }
          </QueryState>
        </Flex>
      )}
    </Page>
  );
}
