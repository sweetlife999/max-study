import { Button, CellHeader, CellList, CellSimple, Flex, Typography } from '@maxhub/max-ui';

import { useConsentMutation } from '../api/queries';
import type { Me } from '../api/types';
import { InlineError } from '../components/states';
import { LanguageSwitch } from '../components/LanguageSwitch';
import { useI18n } from '../i18n/i18n';

export function ConsentScreen({ me }: { me: Me }) {
  const { t } = useI18n();
  const consent = useConsentMutation();

  return (
    <main className="page">
      <Flex direction="column" gap={16}>
        <Typography.Headline variant="medium" asChild>
          <h1 className="page__title">{t('consent.title')}</h1>
        </Typography.Headline>
        <Typography.Body variant="medium">
          {t('consent.intro', { university: me.university.name })}
        </Typography.Body>

        <CellList mode="island" header={<CellHeader>{t('consent.whatHeader')}</CellHeader>}>
          <CellSimple title={t('consent.dataProfile')} />
          <CellSimple title={t('consent.dataActivity')} />
        </CellList>

        <CellList mode="island" header={<CellHeader>{t('consent.whyHeader')}</CellHeader>}>
          <CellSimple title={t('consent.purpose')} />
        </CellList>

        <CellList mode="island" header={<CellHeader>{t('consent.operatorHeader')}</CellHeader>}>
          <CellSimple
            title={me.university.name}
            subtitle={t('consent.operatorNote')}
          />
        </CellList>

        <Typography.Body variant="small" className="muted">
          {t('consent.withdraw')}
        </Typography.Body>

        <LanguageSwitch current={me.lang} />

        <InlineError error={consent.error} />
        <Button
          size="large"
          stretched
          loading={consent.isPending}
          disabled={consent.isPending}
          onClick={() => consent.mutate()}
        >
          {t('consent.accept')}
        </Button>
      </Flex>
    </main>
  );
}
