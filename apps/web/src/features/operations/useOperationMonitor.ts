import { useEffect, useRef, useState } from 'react';

import { ApiError } from '@/api/benchmarks';
import {
  getOperation,
  isOperationTerminal,
  type OperationSnapshot,
  type OperationState,
} from '@/api/operations';

export type OperationalState = OperationState | 'CANCELLING' | 'UNAVAILABLE';

const pollInterval = 800;
const staleAfter = 5000;

type Connection = 'connecting' | 'live' | 'stale' | 'unavailable' | 'terminal';

interface MonitorView {
  snapshot: OperationSnapshot | null;
  connection: Connection;
  message: string;
  animateAfter: number | null;
}

export function reconcileOperation(
  previous: OperationSnapshot | null,
  current: OperationSnapshot,
): OperationSnapshot {
  if (!previous || current.resync_required) return current;
  const retained = previous.events.filter(
    (entry) => entry.sequence >= current.first_available_sequence,
  );
  return { ...current, events: [...retained, ...current.events] };
}

function unavailableMessage(error: unknown) {
  if (!(error instanceof ApiError)) return null;
  switch (error.status) {
    case 404:
      return 'Run unavailable. Its live snapshot may have expired or the server may have restarted.';
    case 401:
    case 403:
      return 'The server denied access to this run.';
    case 400:
    case 422:
      return 'The server rejected this run identifier.';
    default:
      return null;
  }
}

/** Own a single sequential polling session. The caller keys it by run ID. */
export function useOperationMonitor(
  runId: string,
  onState?: (id: string, state: OperationalState) => void,
) {
  const [view, setView] = useState<MonitorView>({
    snapshot: null,
    connection: 'connecting',
    message: '',
    animateAfter: null,
  });
  const [retry, setRetry] = useState(0);
  const verified = useRef<OperationSnapshot | null>(null);

  useEffect(() => {
    const owner = new AbortController();
    let pollTimer: ReturnType<typeof setTimeout>;
    let staleTimer: ReturnType<typeof setTimeout>;
    let failures = 0;
    let interrupted = true;

    function markStale(message: string) {
      interrupted = true;
      setView({
        snapshot: verified.current,
        connection: 'stale',
        message,
        animateAfter: null,
      });
    }

    function schedule(delay: number) {
      pollTimer = setTimeout(() => void poll(), delay);
    }

    function accept(current: OperationSnapshot) {
      const previous = verified.current;
      const snapshot = reconcileOperation(previous, current);
      const terminal = isOperationTerminal(snapshot.state);
      const animateAfter =
        previous && !interrupted && !current.resync_required
          ? previous.snapshot_sequence
          : null;
      verified.current = snapshot;
      interrupted = false;
      setView({
        snapshot,
        connection: terminal ? 'terminal' : 'live',
        message: current.resync_required
          ? 'Event window refreshed. Earlier transitions could not be replayed.'
          : '',
        animateAfter,
      });
      failures = 0;
      onState?.(
        runId,
        !terminal && snapshot.cancel_requested ? 'CANCELLING' : snapshot.state,
      );
      if (terminal) return;
      staleTimer = setTimeout(
        () =>
          markStale(
            'Waiting for a fresh snapshot. Showing the last verified observations.',
          ),
        staleAfter,
      );
      schedule(pollInterval);
    }

    function failed(error: unknown) {
      const message = unavailableMessage(error);
      if (message) {
        if (error instanceof ApiError && error.status === 404)
          onState?.(runId, 'UNAVAILABLE');
        setView({
          snapshot: verified.current,
          connection: 'unavailable',
          message,
          animateAfter: null,
        });
        return;
      }
      markStale(
        verified.current
          ? 'Live updates interrupted. Showing the last verified observations.'
          : 'Waiting for the server to provide a verified snapshot.',
      );
      failures += 1;
      const retryAfter = error instanceof ApiError ? error.retryAfter : 1000;
      schedule(Math.max(retryAfter, Math.min(1000 * 2 ** failures, 15000)));
    }

    async function poll() {
      try {
        const cursor = verified.current?.snapshot_sequence ?? null;
        const current = await getOperation(runId, cursor, owner.signal);
        if (owner.signal.aborted) return;
        clearTimeout(staleTimer);
        accept(current);
      } catch (error) {
        if (owner.signal.aborted) return;
        clearTimeout(staleTimer);
        failed(error);
      }
    }

    if (retry > 0) schedule(0);
    else void poll();
    return () => {
      owner.abort();
      clearTimeout(pollTimer);
      clearTimeout(staleTimer);
    };
  }, [runId, retry, onState]);

  return { ...view, retry: () => setRetry((value) => value + 1) };
}
