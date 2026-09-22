import { Button, CellHeader, CellList, CellSimple, Flex } from '@maxhub/max-ui';
import { useNavigate } from 'react-router';

import { useCompleteStepMutation, useEventsQuery, useOnboardingQuery } from '../api/queries';
import type { Step } from '../api/types';
import { useSession } from '../app/session';
import { EventCell } from '../components/EventCell';
import { Page } from '../components/Page';
import { EmptyState, InlineError, QueryState } from '../components/states';
import { useI18n } from '../i18n/i18n';

const HOME_EVENTS_LIMIT = 5;

export function HomeScreen() {
  const { t } = useI18n();
  const me = useSession();
  const navigate = useNavigate();

  return (
    <Page title={t('home.title', { name: me.first_name })}>
      <Flex direction="column" gap={16} className="page-sections">
        <Flex gap={8} wrap="wrap" className="actions">
          <Button size="large" onClick={() => void navigate('/checkin')}>
            {t('home.checkin')}
          </Button>
          <Button size="large" variant="secondary" onClick={() => void navigate('/profile')}>
            {t('home.profile', { points: me.points })}
          </Button>
        </Flex>

        {(me.is_organizer || me.is_admin) && (
          <CellList
            className="full-width"
            mode="island"
            header={<CellHeader>{t('home.manageHeader')}</CellHeader>}
          >
            {me.is_organizer && (
              <CellSimple
                as="button"
                showChevron
                title={t('org.eventsTitle')}
                onClick={() => void navigate('/org')}
              />
            )}
            {me.is_admin && (
              <CellSimple
                as="button"
                showChevron
                title={t('admin.inviteTitle')}
                onClick={() => void navigate('/admin/invite')}
              />
            )}
          </CellList>
        )}

        <OnboardingSection />
        <UpcomingSection />
      </Flex>
    </Page>
  );
}

function OnboardingSection() {
  const { t } = useI18n();
  const query = useOnboardingQuery();

  return (
    <section aria-labelledby="onboarding-heading">
      <QueryState query={query}>
        {(onboarding) => (
          <CellList
            className="full-width"
            mode="island"
            header={
              <CellHeader
                id="onboarding-heading"
                after={t('onboarding.progress', {
                  done: onboarding.done_count,
                  total: onboarding.total,
                })}
              >
                {t('onboarding.title')}
              </CellHeader>
            }
          >
            <div className="progress-wrap">
              <progress
                className="progress"
                max={Math.max(onboarding.total, 1)}
                value={onboarding.done_count}
                aria-label={t('onboarding.progress', {
                  done: onboarding.done_count,
                  total: onboarding.total,
                })}
              />
            </div>
            {onboarding.steps.length === 0 ? (
              <EmptyState title={t('onboarding.empty')} />
            ) : (
              onboarding.steps.map((step) => <StepCell key={step.key} step={step} />)
            )}
          </CellList>
        )}
      </QueryState>
    </section>
  );
}

function StepCell({ step }: { step: Step }) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const complete = useCompleteStepMutation();
  const interactive = !step.done;
  const isManual = step.type === 'manual';
  const isPending = isManual && complete.isPending;

  const activate = () => {
    if (isManual) {
      complete.mutate(step.key);
      return;
    }
    if (step.event_kind) {
      void navigate(`/events?kind=${encodeURIComponent(step.event_kind)}`);
    }
  };

  return (
    <CellSimple
      as={interactive ? 'button' : undefined}
      disabled={isPending}
      showChevron={interactive && !isManual}
      className={interactive ? 'step-row' : undefined}
      onClick={interactive ? activate : undefined}
      before={
        <span className={step.done ? 'step-mark step-mark_done' : 'step-mark'} aria-hidden>
          {step.done ? '✓' : ''}
        </span>
      }
      title={step.title}
      subtitle={
        <>
          {step.description}
          {step.done && <span className="visually-hidden"> — {t('onboarding.done')}</span>}
          {step.type === 'event_kind' && !step.done && (
            <span className="step-hint"> {t('onboarding.eventHint')}</span>
          )}
          <InlineError error={complete.error} />
        </>
      }
      after={
        step.type === 'manual' && interactive ? (
          <span className="step-action">{t('onboarding.markDone')}</span>
        ) : undefined
      }
    />
  );
}

function UpcomingSection() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const query = useEventsQuery('upcoming');

  return (
    <section aria-labelledby="upcoming-heading">
      <CellList
        className="full-width"
        mode="island"
        header={
          <CellHeader
            id="upcoming-heading"
            after={
              <Button size="small" variant="ghost" onClick={() => void navigate('/events')}>
                {t('home.allEvents')}
              </Button>
            }
          >
            {t('home.upcoming')}
          </CellHeader>
        }
      >
        <QueryState query={query}>
          {({ items }) =>
            items.length === 0 ? (
              <EmptyState title={t('events.emptyUpcoming')} />
            ) : (
              items
                .slice(0, HOME_EVENTS_LIMIT)
                .map((event) => (
                  <EventCell key={event.id} event={event} to={`/events/${event.id}`} />
                ))
            )
          }
        </QueryState>
      </CellList>
    </section>
  );
}
