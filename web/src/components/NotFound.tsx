import { Button } from '@maxhub/max-ui';
import { useNavigate } from 'react-router';

import { useI18n } from '../i18n/i18n';
import { EmptyState } from './states';

export function NotFoundState() {
  const { t } = useI18n();
  const navigate = useNavigate();
  return (
    <EmptyState
      title={t('error.notFound')}
      action={
        <Button variant="secondary" onClick={() => void navigate('/')}>
          {t('common.toHome')}
        </Button>
      }
    />
  );
}
