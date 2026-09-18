import { Button, Flex, Typography } from '@maxhub/max-ui';
import { useState, type ReactNode } from 'react';
import { useNavigate, useParams } from 'react-router';

import {
  useCreateEventMutation,
  useEventQuery,
  useOnboardingQuery,
  useOrgEventsQuery,
  useUpdateEventMutation,
} from '../api/queries';
import type { CreateEventRequest, Event } from '../api/types';
import { useSession } from '../app/session';
import { NotFoundState } from '../components/NotFound';
import { Page } from '../components/Page';
import { InlineError, QueryState } from '../components/states';
import { useI18n } from '../i18n/i18n';
import { collectEventKinds, collectOnboardingSteps } from '../lib/eventKinds';
import { parseId } from '../lib/routeParams';
import { emptyEventForm, eventToForm, validateEventForm, type EventForm } from '../lib/eventForm';

export interface OrgEventFormScreenProps {
  mode: 'create' | 'edit';
}

export function OrgEventFormScreen({ mode }: OrgEventFormScreenProps) {
  const { t } = useI18n();
  const me = useSession();
  const eventId = parseId(useParams().id);
  const title = mode === 'create' ? t('org.createTitle') : t('org.editTitle');
  const timezone = me.university.timezone;

  if (!me.is_organizer) {
    return (
      <Page title={title} backTo="/">
        <NotFoundState />
      </Page>
    );
  }
  if (mode === 'create') {
    return (
      <Page title={title} backTo="/org">
        <CreateForm timezone={timezone} />
      </Page>
    );
  }
  if (eventId === null) {
    return (
      <Page title={title} backTo="/org">
        <NotFoundState />
      </Page>
    );
  }
  return (
    <Page title={title} backTo={`/org/events/${eventId}`}>
      <EditLoader eventId={eventId} timezone={timezone} />
    </Page>
  );
}

function CreateForm({ timezone }: { timezone: string }) {
  const navigate = useNavigate();
  const create = useCreateEventMutation();
  return (
    <EventFormView
      initial={emptyEventForm(timezone)}
      timezone={timezone}
      pending={create.isPending}
      error={create.error}
      onSubmit={(request) => {
        create.mutate(request, {
          onSuccess: (event) => void navigate(`/org/events/${event.id}`, { replace: true }),
        });
      }}
    />
  );
}

function EditLoader({ eventId, timezone }: { eventId: number; timezone: string }) {
  const query = useEventQuery(eventId);
  return (
    <QueryState query={query}>
      {(event) => <EditForm event={event} timezone={timezone} />}
    </QueryState>
  );
}

function EditForm({ event, timezone }: { event: Event; timezone: string }) {
  const navigate = useNavigate();
  const update = useUpdateEventMutation(event.id);
  return (
    <EventFormView
      initial={eventToForm(event, timezone)}
      timezone={timezone}
      pending={update.isPending}
      error={update.error}
      onSubmit={(request) => {
        update.mutate(request, {
          onSuccess: () => void navigate(`/org/events/${event.id}`, { replace: true }),
        });
      }}
    />
  );
}

interface EventFormViewProps {
  initial: EventForm;
  timezone: string;
  pending: boolean;
  error: unknown;
  onSubmit: (request: CreateEventRequest) => void;
}

function EventFormView({ initial, timezone, pending, error, onSubmit }: EventFormViewProps) {
  const { t } = useI18n();
  const [form, setForm] = useState(initial);
  const [showErrors, setShowErrors] = useState(false);
  const orgEvents = useOrgEventsQuery();
  const onboarding = useOnboardingQuery();

  const kinds = collectEventKinds({
    events: orgEvents.data?.items ?? [],
    onboarding: onboarding.data,
  });
  const steps = collectOnboardingSteps(onboarding.data);
  const { errors, request } = validateEventForm(form, timezone);
  const field = <K extends keyof EventForm>(key: K, value: EventForm[K]) =>
    setForm((current) => ({ ...current, [key]: value }));

  const errorFor = (key: keyof typeof errors) => (showErrors ? errors[key] : undefined);

  return (
    <form
      noValidate
      onSubmit={(formEvent) => {
        formEvent.preventDefault();
        setShowErrors(true);
        if (request) onSubmit(request);
      }}
    >
      <Flex direction="column" gap={16}>
        <Field label={t('org.fieldTitle')} error={errorFor('title') && t('org.errTitle')}>
          {(props) => (
            <input
              {...props}
              value={form.title}
              maxLength={200}
              onChange={(e) => field('title', e.target.value)}
            />
          )}
        </Field>

        <Field label={t('org.fieldDescription')}>
          {(props) => (
            <textarea
              {...props}
              value={form.description}
              maxLength={2000}
              onChange={(e) => field('description', e.target.value)}
            />
          )}
        </Field>

        <Field label={t('org.fieldKind')} error={errorFor('kind') && t('org.errKind')}>
          {(props) => (
            <select {...props} value={form.kind} onChange={(e) => field('kind', e.target.value)}>
              <option value="" disabled>
                {t('org.kindPlaceholder')}
              </option>
              {kinds.map((kind) => (
                <option key={kind.key} value={kind.key}>
                  {kind.title}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Typography.Body variant="small" className="muted">
          {t('org.kindsUnavailable')}
        </Typography.Body>

        <Field label={t('org.fieldLocation')}>
          {(props) => (
            <input
              {...props}
              value={form.location}
              maxLength={200}
              onChange={(e) => field('location', e.target.value)}
            />
          )}
        </Field>

        <Field label={t('org.fieldStarts')} error={errorFor('startsAt') && t('org.errStarts')}>
          {(props) => (
            <input
              {...props}
              type="datetime-local"
              value={form.startsAt}
              onChange={(e) => field('startsAt', e.target.value)}
            />
          )}
        </Field>

        <Field
          label={t('org.fieldEnds')}
          error={
            errorFor('endsAt') &&
            (errors.endsAt === 'before_start' ? t('org.errEndsBeforeStarts') : t('org.errEnds'))
          }
        >
          {(props) => (
            <input
              {...props}
              type="datetime-local"
              value={form.endsAt}
              onChange={(e) => field('endsAt', e.target.value)}
            />
          )}
        </Field>

        <Typography.Body variant="small" className="muted">
          {t('org.timezoneNote', { timezone })}
        </Typography.Body>

        <Field label={t('org.fieldPoints')} error={errorFor('points') && t('org.errPoints')}>
          {(props) => (
            <input
              {...props}
              inputMode="numeric"
              value={form.points}
              onChange={(e) => field('points', e.target.value)}
            />
          )}
        </Field>

        <Field label={t('org.fieldStep')}>
          {(props) => (
            <select
              {...props}
              value={form.onboardingStep}
              onChange={(e) => field('onboardingStep', e.target.value)}
            >
              <option value="">{t('org.stepNone')}</option>
              {steps.map((step) => (
                <option key={step.key} value={step.key}>
                  {step.title}
                </option>
              ))}
            </select>
          )}
        </Field>

        <InlineError error={error} />
        <Button type="submit" size="large" stretched loading={pending} disabled={pending}>
          {t('common.save')}
        </Button>
      </Flex>
    </form>
  );
}

interface FieldControlProps {
  className: string;
  'aria-invalid': boolean;
  'aria-label': string;
}

/** A labelled control with an optional validation message underneath. */
function Field({
  label,
  error,
  children,
}: {
  label: string;
  error?: string | undefined;
  children: (props: FieldControlProps) => ReactNode;
}) {
  const invalid = Boolean(error);
  return (
    <label className="field">
      <span className="field__label">{label}</span>
      {children({ className: 'field__control', 'aria-invalid': invalid, 'aria-label': label })}
      {error && (
        <span className="field__error" role="alert">
          {error}
        </span>
      )}
    </label>
  );
}
