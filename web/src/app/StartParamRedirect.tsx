import { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router';

import { useBridge } from '../bridge/context';
import { parseCheckinStartParam } from '../lib/checkinPayload';
import { useCheckinPayload } from './checkinPayload';
import { startParamRoute } from '../lib/startParam';
import { useConsentDuringLaunch } from './consentLaunch';

/**
 * Opens the route requested by `initDataUnsafe.start_param` (§9) exactly once per app launch,
 * so that "go home" from the result screen does not bounce back into the check-in.
 */
export function StartParamRedirect() {
  const bridge = useBridge();
  const navigate = useNavigate();
  const { setPayload } = useCheckinPayload();
  const consentDuringLaunch = useConsentDuringLaunch();
  const handled = useRef(false);

  useEffect(() => {
    if (handled.current) return;
    handled.current = true;
    const payload = parseCheckinStartParam(bridge.startParam);
    if (payload && consentDuringLaunch) {
      void navigate(`/checkin?event=${payload.eventId}&fresh=1`, { replace: true });
      return;
    }
    if (payload) setPayload(payload);
    const target = startParamRoute(bridge.startParam);
    if (target !== null) void navigate(target, { replace: true });
  }, [bridge, consentDuringLaunch, navigate, setPayload]);

  return null;
}
