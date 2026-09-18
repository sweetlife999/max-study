import { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router';

import { useBridge } from '../bridge/context';
import { parseCheckinStartParam } from '../lib/checkinPayload';
import { useCheckinPayload } from './checkinPayload';
import { startParamRoute } from '../lib/startParam';

/**
 * Opens the route requested by `initDataUnsafe.start_param` (§9) exactly once per app launch,
 * so that "go home" from the result screen does not bounce back into the check-in.
 */
export function StartParamRedirect() {
  const bridge = useBridge();
  const navigate = useNavigate();
  const { setPayload } = useCheckinPayload();
  const handled = useRef(false);

  useEffect(() => {
    if (handled.current) return;
    handled.current = true;
    const payload = parseCheckinStartParam(bridge.startParam);
    if (payload) setPayload(payload);
    const target = startParamRoute(bridge.startParam);
    if (target !== null) void navigate(target, { replace: true });
  }, [bridge, navigate, setPayload]);

  return null;
}
