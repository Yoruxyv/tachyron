import { useCallback, useRef, useState } from 'react';

import ConnectionTroubleshooting from './ConnectionTroubleshooting';
import type { useApiHealth } from './hooks/useApiHealth';

export default function WorkspaceStatus({
  health,
}: {
  readonly health: ReturnType<typeof useApiHealth>;
}) {
  const [troubleshooting, setTroubleshooting] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const status = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => setTroubleshooting(false), []);
  const restoreFocus = useCallback(() => {
    const target = trigger.current?.isConnected
      ? trigger.current
      : status.current;
    target?.focus({ preventScroll: true });
  }, []);
  let connection = 'API status unknown';
  if (health.checking) connection = 'API checking…';
  else if (health.status === 'API online') connection = 'API online';
  else if (health.status !== 'Not checked') connection = 'API offline';
  return (
    <div className="sidebar-footer">
      <button
        ref={status}
        className="workspace-connection"
        type="button"
        onClick={health.check}
        aria-disabled={health.checking}
        aria-busy={health.checking}
        aria-label="Recheck API connection"
        aria-describedby="workspace-api-status"
        title="Last connection check. Availability does not establish benchmark readiness. Activate to recheck."
      >
        <span
          className="workspace-dot"
          data-live={health.status === 'API online' && !health.checking}
          aria-hidden="true"
        />
        <span id="workspace-api-status">{connection}</span>
      </button>
      {(connection === 'API offline' || troubleshooting) && (
        <button
          ref={trigger}
          type="button"
          className="workspace-troubleshoot"
          aria-haspopup="dialog"
          aria-expanded={troubleshooting}
          onClick={() => setTroubleshooting(true)}
        >
          Troubleshoot
        </button>
      )}
      {troubleshooting && (
        <ConnectionTroubleshooting
          health={health}
          connection={connection}
          onClose={close}
          restoreFocus={restoreFocus}
        />
      )}
    </div>
  );
}
