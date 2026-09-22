import { Button, CellList, Flex } from '@maxhub/max-ui';
import { useSearchParams } from 'react-router';

import { useEventsQuery } from '../api/queries';
import type { EventScope } from '../api/types';
import { EventCell } from '../components/EventCell';
import { Page } from '../components/Page';
import { EmptyState, QueryState } from '../components/states';
import { useI18n } from '../i18n/i18n';

export function EventsScreen() {
  const { t } = useI18n();
  const [params, setParams] = useSearchParams();
  const scope: EventScope = params.get('scope') === 'past' ? 'past' : 'upcoming';
  const kind = params.get('kind');
  const query = useEventsQuery(scope);

  const tab = (value: EventScope, label: string) => (
    <Button
      size="medium"
      variant={scope === value ? 'primary' : 'secondary'}
      aria-pressed={scope === value}
      onClick={() => setParams(value === 'upcoming' ? {} : { scope: value }, { replace: true })}
    >
      {label}
    </Button>
  );

  return (
    <Page title={t('events.title')} backTo="/">
      <Flex direction="column" gap={16}>
        <Flex gap={8} role="group" aria-label={t('events.scopeLabel')}>
          {tab('upcoming', t('events.upcoming'))}
          {tab('past', t('events.past'))}
        </Flex>
        <QueryState query={query}>
          {({ items }) => {
            const filtered = kind ? items.filter((event) => event.kind === kind) : items;
            return filtered.length === 0 ? (
              <EmptyState
                title={
                  kind
                    ? t('events.emptyMatching')
                    : scope === 'past'
                      ? t('events.emptyPast')
                      : t('events.emptyUpcoming')
                }
              />
            ) : (
              <CellList mode="island">
                {filtered.map((event) => (
                  <EventCell key={event.id} event={event} to={`/events/${event.id}`} />
                ))}
              </CellList>
            );
          }}
        </QueryState>
      </Flex>
    </Page>
  );
}
