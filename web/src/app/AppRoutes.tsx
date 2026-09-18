import { Route, Routes } from 'react-router';

import { NotFoundState } from '../components/NotFound';
import { Page } from '../components/Page';
import { useI18n } from '../i18n/i18n';
import { AdminInviteScreen } from '../screens/AdminInviteScreen';
import { AutoCheckinScreen } from '../screens/AutoCheckinScreen';
import { CheckinScreen } from '../screens/CheckinScreen';
import { EventScreen } from '../screens/EventScreen';
import { EventsScreen } from '../screens/EventsScreen';
import { HomeScreen } from '../screens/HomeScreen';
import { InvalidCheckinLink } from '../screens/InvalidCheckinLink';
import { OrgAttendanceScreen } from '../screens/OrgAttendanceScreen';
import { OrgEventFormScreen } from '../screens/OrgEventFormScreen';
import { OrgEventScreen } from '../screens/OrgEventScreen';
import { OrgEventsScreen } from '../screens/OrgEventsScreen';
import { OrgQrScreen } from '../screens/OrgQrScreen';
import { ProfileScreen } from '../screens/ProfileScreen';
import { StartParamRedirect } from './StartParamRedirect';

export function AppRoutes() {
  const { t } = useI18n();
  return (
    <>
      <StartParamRedirect />
      <Routes>
        <Route path="/" element={<HomeScreen />} />
        <Route path="/events" element={<EventsScreen />} />
        <Route path="/events/:id" element={<EventScreen />} />
        <Route path="/checkin" element={<CheckinScreen />} />
        <Route path="/checkin/qr/:eventId/:code" element={<AutoCheckinScreen />} />
        <Route
          path="/checkin/invalid"
          element={
            <Page title={t('checkin.title')} backTo="/">
              <InvalidCheckinLink />
            </Page>
          }
        />
        <Route path="/profile" element={<ProfileScreen />} />
        <Route path="/org" element={<OrgEventsScreen />} />
        <Route path="/org/events/new" element={<OrgEventFormScreen mode="create" />} />
        <Route path="/org/events/:id" element={<OrgEventScreen />} />
        <Route path="/org/events/:id/edit" element={<OrgEventFormScreen mode="edit" />} />
        <Route path="/org/events/:id/qr" element={<OrgQrScreen />} />
        <Route path="/org/events/:id/attendance" element={<OrgAttendanceScreen />} />
        <Route path="/admin/invite" element={<AdminInviteScreen />} />
        <Route
          path="*"
          element={
            <Page title={t('error.notFound')} backTo="/">
              <NotFoundState />
            </Page>
          }
        />
      </Routes>
    </>
  );
}
