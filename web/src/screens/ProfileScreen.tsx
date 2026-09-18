import { CellList, CellSimple, Flex } from '@maxhub/max-ui';

import { useSession } from '../app/session';
import { LanguageSwitch } from '../components/LanguageSwitch';
import { Page } from '../components/Page';
import { useI18n } from '../i18n/i18n';

export function ProfileScreen() {
  const { t } = useI18n();
  const me = useSession();

  const roles = [
    t('profile.roleStudent'),
    me.is_organizer ? t('profile.roleOrganizer') : null,
    me.is_admin ? t('profile.roleAdmin') : null,
  ].filter((role): role is string => role !== null);

  return (
    <Page title={t('profile.title')} backTo="/">
      <Flex direction="column" gap={16}>
        <CellList mode="island">
          <CellSimple overline={t('profile.points')} title={String(me.points)} />
          <CellSimple overline={t('profile.university')} title={me.university.name} />
          <CellSimple overline={t('profile.timezone')} title={me.university.timezone} />
          <CellSimple overline={t('profile.roleHeader')} title={roles.join(' · ')} />
        </CellList>

        <section className="panel">
          <LanguageSwitch current={me.lang} />
        </section>
      </Flex>
    </Page>
  );
}
