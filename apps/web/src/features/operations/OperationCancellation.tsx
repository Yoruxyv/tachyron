import { useEffect, useRef, useState } from 'react';

import {
  cancelOperation,
  isOperationTerminal,
  type OperationSnapshot,
} from '@/api/operations';

export default function OperationCancellation({
  snapshot,
  live,
}: {
  readonly snapshot: OperationSnapshot;
  readonly live: boolean;
}) {
  const [sending, setSending] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [error, setError] = useState('');
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  if (isOperationTerminal(snapshot.state) || snapshot.cancel_requested)
    return null;

  async function cancel() {
    if (!live || request.current || acknowledged) return;
    const owner = new AbortController();
    request.current = owner;
    setSending(true);
    setError('');
    try {
      await cancelOperation(snapshot.run_id, owner.signal);
      if (!owner.signal.aborted) setAcknowledged(true);
    } catch {
      if (!owner.signal.aborted)
        setError(
          'The cancellation response could not be verified. Watch the run status before requesting cancellation again.',
        );
    } finally {
      if (!owner.signal.aborted) setSending(false);
      request.current = null;
    }
  }

  return (
    <div className="monitor-status">
      {acknowledged ? (
        <output>
          Server response received. Waiting for the latest run status.
        </output>
      ) : (
        <button
          type="button"
          onClick={() => void cancel()}
          disabled={!live || sending}
        >
          {sending ? 'Requesting cancellation…' : 'Cancel operation'}
        </button>
      )}
      {error && (
        <p role="alert" className="monitor-notice">
          {error}
        </p>
      )}
    </div>
  );
}
