import { Button, CellHeader, CellList, CellSimple, Flex, Typography } from '@maxhub/max-ui';
import { useState } from 'react';

import { useCreateInviteMutation } from '../api/queries';
import type { Invite } from '../api/types';
import { useSession } from '../app/session';
import { useBridge } from '../bridge/context';
import { NotFoundState } from '../components/NotFound';
import { Page } from '../components/Page';
import { InlineError } from '../components/states';
import { useI18n } from '../i18n/i18n';
import { copyText } from '../lib/clipboard';
import { formatDateTime } from '../lib/datetime';

export function AdminInviteScreen() {
  const { t } = useI18n();
  const me = useSession();
  const invite = useCreateInviteMutation();

  return (
    <Page title={t('admin.inviteTitle')} backTo="/">
      {!me.is_admin ? (
        <NotFoundState />
      ) : (
        <Flex direction="column" gap={16}>
          <Typography.Body variant="medium">{t('admin.inviteIntro')}</Typography.Body>
          {invite.data && <InviteView invite={invite.data} />}
          <InlineError error={invite.error} />
          <Button
            size="large"
            stretched
            loading={invite.isPending}
            disabled={invite.isPending}
            onClick={() => invite.mutate()}
          >
            {invite.data ? t('admin.createAnother') : t('admin.create')}
          </Button>
        </Flex>
      )}
    </Page>
  );
}

function InviteView({ invite }: { invite: Invite }) {
  const { t, lang } = useI18n();
  const me = useSession();
  const bridge = useBridge();
  const [copied, setCopied] = useState(false);
  const [shareNotice, setShareNotice] = useState<'unavailable' | 'failed' | null>(null);

  const share = async () => {
    setShareNotice(null);
    const outcome = await bridge.share({ text: t('admin.inviteTitle'), link: invite.deeplink });
    if (outcome !== 'shared') setShareNotice(outcome === 'failed' ? 'failed' : 'unavailable');
  };

  return (
    <Flex direction="column" gap={12}>
      <CellList mode="island" header={<CellHeader>{t('admin.deeplinkHeader')}</CellHeader>}>
        <CellSimple
          title={<span className="break-all">{invite.deeplink}</span>}
          subtitle={t('admin.expiresAt', {
            date: formatDateTime(invite.expires_at, me.university.timezone, lang),
          })}
        />
      </CellList>

      <Flex gap={8} wrap="wrap" className="actions">
        <Button
          size="medium"
          variant="secondary"
          onClick={() => {
            void copyText(invite.deeplink).then(setCopied);
          }}
        >
          {copied ? t('common.copied') : t('common.copy')}
        </Button>
        <Button size="medium" onClick={() => void share()}>
          {t('admin.share')}
        </Button>
      </Flex>

      {shareNotice !== null && (
        <Typography.Body variant="small" role="alert">
          {shareNotice === 'failed' ? t('admin.shareFailed') : t('admin.shareUnavailable')}
        </Typography.Body>
      )}
    </Flex>
  );
}
