import type {
  AppConfigView,
  AttendanceEntry,
  AttendanceView,
  CheckinRequest as GeneratedCheckinRequest,
  CheckinResult as GeneratedCheckinResult,
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
import type {
  ApiErrorBody,
  AppConfig,
  Attendance,
  AttendanceItem,
  CheckinMethod,
  CheckinRequest,
  CheckinResult,
  CreateEventRequest,
  Event,
  EventList,
  EventQr,
  EventScope,
  Invite,
  Lang,
  Me,
  Onboarding,
  Step,
  University,
  UpdateEventRequest,
  UpdateMeRequest,
} from './types';

type Equal<Left, Right> = [Left] extends [Right] ? ([Right] extends [Left] ? true : false) : false;
type Extends<Left, Right> = [Left] extends [Right] ? true : false;
type Assert<Condition extends true> = Condition;
type GeneratedEventScope = NonNullable<NonNullable<EventsApiEventsGetData['query']>['scope']>;

/**
 * Compile-only contract: public application types stay backed by generated OpenAPI types.
 * Narrower ergonomic adapters are allowed, but none may accept values rejected by the schema.
 */
export type ApiTypeContract = [
  Assert<Equal<Lang, UpdateMe['lang']>>,
  Assert<Equal<CheckinMethod, GeneratedCheckinRequest['method']>>,
  Assert<Equal<EventScope, GeneratedEventScope>>,
  Assert<Equal<University, UniversityView>>,
  Assert<Equal<Event, EventView>>,
  Assert<Equal<EventList, GeneratedEventList>>,
  Assert<Equal<EventQr, QrCodeView>>,
  Assert<Equal<Invite, InviteView>>,
  Assert<Equal<ApiErrorBody, ErrorBody>>,
  Assert<Extends<Me, MeView>>,
  Assert<Extends<Step, StepView>>,
  Assert<Extends<Onboarding, OnboardingView>>,
  Assert<Extends<CheckinRequest, GeneratedCheckinRequest>>,
  Assert<Extends<CheckinResult, GeneratedCheckinResult>>,
  Assert<Extends<CreateEventRequest, CreateEvent>>,
  Assert<Extends<UpdateEventRequest, UpdateEvent>>,
  Assert<Extends<AttendanceItem, AttendanceEntry>>,
  Assert<Extends<Attendance, AttendanceView>>,
  Assert<Extends<AppConfig, AppConfigView>>,
  Assert<Extends<UpdateMeRequest, UpdateMe>>,
  Assert<Equal<keyof Me, keyof MeView>>,
  Assert<Equal<keyof Step, keyof StepView>>,
  Assert<Equal<keyof CreateEventRequest, keyof CreateEvent>>,
  Assert<Equal<keyof UpdateEventRequest, keyof UpdateEvent>>,
  Assert<Equal<keyof AttendanceItem, keyof AttendanceEntry>>,
  Assert<Equal<keyof AppConfig, keyof AppConfigView>>,
];
