import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiError } from '@/api/benchmarks';
import {
  isOperationTerminal,
  isRunId,
  startOperation,
  type OperationMode,
  type OperationRequest,
} from '@/api/operations';

import type { OperationalState } from './useOperationMonitor';

const storageKey = 'tachyron.operation.v1';

type Recovery =
  | { kind: 'accepted'; runId: string; mode: OperationMode }
  | { kind: 'uncertain'; mode: OperationMode };

function recover(): Recovery | null {
  try {
    const value: unknown = JSON.parse(
      sessionStorage.getItem(storageKey) ?? 'null',
    );
    if (
      !value ||
      typeof value !== 'object' ||
      !('mode' in value) ||
      (value.mode !== 'manual' && value.mode !== 'adaptive') ||
      !('kind' in value)
    )
      return null;
    if (value.kind === 'uncertain')
      return { kind: value.kind, mode: value.mode };
    if (
      value.kind === 'accepted' &&
      'runId' in value &&
      typeof value.runId === 'string' &&
      isRunId(value.runId)
    ) {
      return { kind: value.kind, mode: value.mode, runId: value.runId };
    }
  } catch {
    // Storage may be disabled. The mounted workspace can still follow a run.
  }
  return null;
}

function preserve(value: Recovery | null) {
  try {
    if (value) sessionStorage.setItem(storageKey, JSON.stringify(value));
    else sessionStorage.removeItem(storageKey);
  } catch {
    // Keep in-memory ownership when this browser disallows storage.
  }
}

function rejection(error: unknown) {
  if (!(error instanceof ApiError)) return null;
  switch (error.status) {
    case 409:
      return 'The server declined this run because another workload is active. Wait for it to finish before starting again.';
    case 400:
    case 422:
      return 'The server rejected this configuration. Check the dataset, dimensions, and worker count.';
    case 404:
      return 'That dataset is unavailable. Choose a registered dataset and try again.';
    case 401:
    case 403:
      return 'The server denied this request. Check your connection to the local API.';
    case 429:
      return 'The server is limiting requests. Wait before starting again.';
    default:
      return null;
  }
}

/** Own admission across dashboard navigation and remember the accepted ID through reloads. */
export function useOperationalExecution() {
  const [recovery, setRecovery] = useState(recover);
  const admission = useRef(recovery);
  const [observed, setObserved] = useState<{
    id: string;
    state: OperationalState;
  } | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');
  const request = useRef<AbortController | null>(null);
  const runId = recovery?.kind === 'accepted' ? recovery.runId : null;
  const status =
    observed && runId && observed.id === runId ? observed.state : null;
  const running =
    runId !== null &&
    (status === null ||
      status === 'CANCELLING' ||
      (status !== 'UNAVAILABLE' && !isOperationTerminal(status)));
  const uncertain = recovery?.kind === 'uncertain';

  useEffect(() => () => request.current?.abort(), []);

  function remember(value: Recovery | null) {
    admission.current = value;
    preserve(value);
    setRecovery(value);
  }

  async function start(body: OperationRequest) {
    if (
      request.current ||
      admission.current !== recovery ||
      running ||
      uncertain
    )
      return;
    const owner = new AbortController();
    request.current = owner;
    setError('');
    setSending(true);
    const unresolved: Recovery = { kind: 'uncertain', mode: body.mode };
    // Record uncertainty before dispatch. Reload cannot safely repeat a POST.
    preserve(unresolved);
    try {
      const snapshot = await startOperation(body, owner.signal);
      if (owner.signal.aborted) return;
      const accepted: Recovery = {
        kind: 'accepted',
        runId: snapshot.run_id,
        mode: body.mode,
      };
      remember(accepted);
    } catch (failure) {
      if (owner.signal.aborted) return;
      const message = rejection(failure);
      if (message) {
        preserve(recovery);
        setError(message);
      } else {
        remember(unresolved);
      }
    } finally {
      if (!owner.signal.aborted) setSending(false);
      request.current = null;
    }
  }

  function clearUncertainty() {
    if (!uncertain || request.current) return;
    remember(null);
  }

  const observe = useCallback((id: string, state: OperationalState) => {
    setObserved((current) =>
      current?.id === id && current.state === state ? current : { id, state },
    );
  }, []);

  return {
    runId,
    mode: recovery?.mode,
    running,
    sending,
    error,
    uncertain,
    start,
    clearUncertainty,
    observe,
    status,
  };
}
