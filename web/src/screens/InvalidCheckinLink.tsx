import { Button, Flex, Typography } from '@maxhub/max-ui';
import { useNavigate } from 'react-router';

import { useI18n } from '../i18n/i18n';

/** Shown when a check-in link or QR does not match `ci_<event>_<6 digits>`. */
export function InvalidCheckinLink() {
  const { t } = useI18n();
  const navigate = useNavigate();
  return (
    <Flex direction="column" gap={16} className="result result_error">
      <div className="result__icon" aria-hidden>
        !
      </div>
      <Typography.Headline variant="small" asChild>
        <h2 role="alert">{t('checkin.invalidLinkTitle')}</h2>
      </Typography.Headline>
      <Typography.Body variant="medium">{t('checkin.invalidLinkText')}</Typography.Body>
      <Flex direction="column" gap={8}>
        <Button size="large" stretched onClick={() => void navigate('/checkin')}>
          {t('checkin.enterManually')}
        </Button>
        <Button size="large" variant="secondary" stretched onClick={() => void navigate('/')}>
          {t('common.toHome')}
        </Button>
      </Flex>
    </Flex>
  );
}
