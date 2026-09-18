import type { Event, Onboarding } from '../api/types';

/**
 * CONTRACT GAP (docs/ARCHITECTURE.md §7): there is no endpoint that lists the `event_kinds`
 * of the university config (§6), yet `POST /api/org/events` requires a `kind` from that list.
 * Until the contract gains a catalogue endpoint, the organizer form offers every kind the
 * client can observe:
 *
 *   - `kind` / `kind_title` of every event already loaded (authoritative, localized titles);
 *   - `event_kind` of the onboarding steps from `GET /api/onboarding`;
 *   - the keys of the example config as a last resort, shown by key.
 *
 * TODO: replace with `GET /api/event-kinds` (or the generated OpenAPI types) once §7 defines it.
 */
export const FALLBACK_EVENT_KIND_KEYS: readonly string[] = ['council', 'curator_meeting', 'club'];

export interface EventKindOption {
  key: string;
  /** Localized title if the server ever sent one for this kind, otherwise the raw key. */
  title: string;
}

export interface EventKindSources {
  events: readonly Event[];
  onboarding: Onboarding | undefined;
}

/** Merges every observed activity kind into one de-duplicated, title-preferring list. */
export function collectEventKinds({ events, onboarding }: EventKindSources): EventKindOption[] {
  const titles = new Map<string, string>();

  const remember = (key: string | null | undefined, title?: string) => {
    if (typeof key !== 'string' || key === '') return;
    const known = titles.get(key);
    if (known === undefined || known === key) titles.set(key, title ?? known ?? key);
  };

  for (const event of events) remember(event.kind, event.kind_title || event.kind);
  for (const step of onboarding?.steps ?? []) remember(step.event_kind);
  for (const key of FALLBACK_EVENT_KIND_KEYS) remember(key);

  return [...titles.entries()]
    .map(([key, title]) => ({ key, title }))
    .sort((a, b) => a.title.localeCompare(b.title));
}

export interface OnboardingStepOption {
  key: string;
  title: string;
}

/** Onboarding steps an organizer may attach an event to (`onboarding_step` of §7). */
export function collectOnboardingSteps(onboarding: Onboarding | undefined): OnboardingStepOption[] {
  return (onboarding?.steps ?? []).map((step) => ({ key: step.key, title: step.title }));
}
