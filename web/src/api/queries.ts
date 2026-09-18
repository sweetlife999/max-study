import {
  QueryCache,
  QueryClient,
  MutationCache,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';

import { useApi } from './context';
import { isApiError } from './errors';
import type { CreateEventRequest, Event, EventScope, Lang, Me, UpdateEventRequest } from './types';

export const ATTENDANCE_POLL_MS = 5000;
const MAX_RETRIES = 2;

export const queryKeys = {
  me: ['me'] as const,
  onboarding: ['onboarding'] as const,
  events: (scope: EventScope) => ['events', scope] as const,
  event: (id: number) => ['event', id] as const,
  orgEvents: ['org', 'events'] as const,
  attendance: (id: number) => ['org', 'attendance', id] as const,
};

/**
 * Shared QueryClient policy: 4xx answers are final (no retries); any `403 consent_required`
 * flips the cached profile to "no consent", which routes the user to the consent screen.
 */
export function createQueryClient(): QueryClient {
  const onError = (error: unknown) => {
    if (isApiError(error) && error.isConsentRequired) {
      client.setQueryData<Me>(queryKeys.me, (me) => (me ? { ...me, consent: false } : me));
    }
  };
  const client: QueryClient = new QueryClient({
    queryCache: new QueryCache({ onError }),
    mutationCache: new MutationCache({ onError }),
    defaultOptions: {
      queries: {
        retry: (failureCount, error) =>
          !(isApiError(error) && error.isClientError) && failureCount < MAX_RETRIES,
        refetchOnWindowFocus: false,
        staleTime: 15_000,
      },
      mutations: { retry: false },
    },
  });
  return client;
}

export function useMeQuery() {
  const api = useApi();
  return useQuery({ queryKey: queryKeys.me, queryFn: api.getMe, staleTime: 60_000 });
}

export function useOnboardingQuery() {
  const api = useApi();
  return useQuery({ queryKey: queryKeys.onboarding, queryFn: api.getOnboarding });
}

export function useEventsQuery(scope: EventScope) {
  const api = useApi();
  return useQuery({ queryKey: queryKeys.events(scope), queryFn: () => api.listEvents(scope) });
}

export function useEventQuery(id: number) {
  const api = useApi();
  return useQuery({ queryKey: queryKeys.event(id), queryFn: () => api.getEvent(id) });
}

export function useOrgEventsQuery() {
  const api = useApi();
  return useQuery({ queryKey: queryKeys.orgEvents, queryFn: api.listOrgEvents });
}

export function useAttendanceQuery(id: number, options: { poll?: boolean } = {}) {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.attendance(id),
    queryFn: () => api.getAttendance(id),
    refetchInterval: options.poll ? ATTENDANCE_POLL_MS : false,
    staleTime: 0,
  });
}

/** Puts a fresh Event into every cache that may show it. */
function useStoreEvent() {
  const queryClient = useQueryClient();
  return (event: Event) => {
    queryClient.setQueryData(queryKeys.event(event.id), event);
    void queryClient.invalidateQueries({ queryKey: ['events'] });
    void queryClient.invalidateQueries({ queryKey: queryKeys.orgEvents });
  };
}

export function useConsentMutation() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.giveConsent,
    onSuccess: (me) => {
      queryClient.setQueryData(queryKeys.me, me);
      // Anything fetched before consent failed with 403 — refetch it now.
      void queryClient.invalidateQueries({ predicate: (q) => q.queryKey[0] !== 'me' });
    },
  });
}

export function useLangMutation() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (lang: Lang) => api.updateLang(lang),
    onSuccess: (me) => {
      queryClient.setQueryData(queryKeys.me, me);
      // Server-side titles (steps, kinds) are localized: reload them.
      void queryClient.invalidateQueries({ predicate: (q) => q.queryKey[0] !== 'me' });
    },
  });
}

export function useCompleteStepMutation() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (key: string) => api.completeStep(key),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.onboarding }),
  });
}

export function useRsvpMutation(eventId: number) {
  const api = useApi();
  const storeEvent = useStoreEvent();
  return useMutation({
    mutationFn: (going: boolean) => api.setRsvp(eventId, going),
    onSuccess: storeEvent,
  });
}

export function useCheckinMutation() {
  const api = useApi();
  const storeEvent = useStoreEvent();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.checkin,
    onSuccess: (result) => {
      storeEvent(result.event);
      queryClient.setQueryData<Me>(queryKeys.me, (me) =>
        me ? { ...me, points: result.points_total } : me,
      );
      void queryClient.invalidateQueries({ queryKey: queryKeys.onboarding });
    },
  });
}

export function useCreateEventMutation() {
  const api = useApi();
  const storeEvent = useStoreEvent();
  return useMutation({
    mutationFn: (request: CreateEventRequest) => api.createOrgEvent(request),
    onSuccess: storeEvent,
  });
}

export function useUpdateEventMutation(eventId: number) {
  const api = useApi();
  const storeEvent = useStoreEvent();
  return useMutation({
    mutationFn: (request: UpdateEventRequest) => api.updateOrgEvent(eventId, request),
    onSuccess: storeEvent,
  });
}

export function useSendQrToChatMutation(eventId: number) {
  const api = useApi();
  return useMutation({ mutationFn: () => api.sendQrToChat(eventId) });
}

export function useCreateInviteMutation() {
  const api = useApi();
  return useMutation({ mutationFn: api.createInvite });
}
