import type {
  AppConfigView,
  AttendanceEntry,
  AttendanceView,
  CheckinRequest as GeneratedCheckinRequest,
  CheckinResult as GeneratedCheckinResult,
  ConfigStepView,
  CreateEvent,
  ErrorBody,
  EventList as GeneratedEventList,
  EventsApiEventsGetData,
  EventView,
  InviteView,
  MeView,
  OnboardingView,
  QrCodeView,
  StepView,
  UniversityView,
  UpdateEvent,
  UpdateMe,
} from './generated/types.gen';

type Replace<Base, Replacements> = Omit<Base, keyof Replacements> & Replacements;

export type Lang = UpdateMe['lang'];

export type University = UniversityView;

/** The API schema currently models this field as a string; keep the app's supported values narrow. */
export type OnboardingStepType = 'event_kind' | 'manual';

export type Me = Replace<MeView, { lang: Lang }>;

export type UpdateMeRequest = UpdateMe;

export type Step = Replace<StepView, { type: OnboardingStepType }>;

export type Onboarding = Replace<OnboardingView, { steps: Step[] }>;

export type Event = EventView;

export type EventList = GeneratedEventList;

export type EventScope = NonNullable<NonNullable<EventsApiEventsGetData['query']>['scope']>;

export type CheckinMethod = GeneratedCheckinRequest['method'];

export type CheckinRequest = GeneratedCheckinRequest;

export type CheckinResult = Replace<GeneratedCheckinResult, { completed_step?: Step | null }>;

/** Keep the existing client contract, which never sends a null points value. */
export type CreateEventRequest = Replace<
  CreateEvent,
  {
    description: NonNullable<CreateEvent['description']>;
    location: NonNullable<CreateEvent['location']>;
    points?: Exclude<CreateEvent['points'], null | undefined>;
  }
>;

/** Keep the existing client contract: only onboarding_step may be explicitly cleared with null. */
export type UpdateEventRequest = {
  [Key in keyof UpdateEvent]?: Key extends 'onboarding_step'
    ? UpdateEvent[Key]
    : Exclude<UpdateEvent[Key], null | undefined>;
};

export type EventQr = QrCodeView;

export type AttendanceItem = Replace<AttendanceEntry, { method: CheckinMethod }>;

export type Attendance = Replace<AttendanceView, { items: AttendanceItem[] }>;

export type Invite = InviteView;

export type ApiErrorBody = ErrorBody;

type ConfigStep = Replace<ConfigStepView, { type: OnboardingStepType }>;

export type AppConfig = Replace<
  AppConfigView,
  { languages: Lang[]; onboarding_steps: ConfigStep[] }
>;
