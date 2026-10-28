import { useCallback, useEffect, useRef, useState } from 'react';

import { getHealth } from '@/api/health';

/** Share validated connection evidence without implying full service readiness. */
export function useApiHealth(verifiedResponseAt = 0) {
  const [status, setStatus] = useState('Not checked');
  const [checking, setChecking] = useState(false);
  const [failedAt, setFailedAt] = useState(0);
  const request = useRef<AbortController | null>(null);
  useEffect(() => {
    if (verifiedResponseAt > failedAt) setStatus('API online');
  }, [verifiedResponseAt, failedAt]);
  useEffect(
    () => () => {
      request.current?.abort();
      request.current = null;
    },
    [],
  );
  const check = useCallback(async () => {
    if (request.current) return false;
    const controller = new AbortController();
    request.current = controller;
    setChecking(true);
    try {
      await getHealth(controller.signal);
      if (!controller.signal.aborted) setStatus('API online');
      return !controller.signal.aborted;
    } catch {
      if (!controller.signal.aborted) {
        setFailedAt(performance.now());
        setStatus(
          'API unavailable. Check that the local server is running, then retry.',
        );
      }
      return false;
    } finally {
      if (request.current === controller) request.current = null;
      if (!controller.signal.aborted) setChecking(false);
    }
  }, []);
  return { status, checking, failedAt, check };
}
