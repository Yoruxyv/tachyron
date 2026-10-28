import { useEffect, useMemo, useRef, useState } from 'react';

import { ApiError, cancelJob, getJob, startJob } from '@/api/benchmarks';
import { isTerminal, type Job, type RunRequest } from '@/api/contracts';
import { parseRequest } from '@/api/decode';

interface Submission {
  key: string;
  request: RunRequest;
}
interface Recovery {
  id: string | null;
  submission: Submission | null;
}
const storageKey = 'tachyron.active.v1';
function recover(): Recovery {
  try {
    const value: unknown = JSON.parse(
      sessionStorage.getItem(storageKey) ?? 'null',
    );
    if (typeof value !== 'object' || value === null)
      return { id: null, submission: null };
    if (
      'id' in value &&
      typeof value.id === 'string' &&
      /^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/iu.test(value.id)
    )
      return { id: value.id, submission: null };
    if (
      'key' in value &&
      typeof value.key === 'string' &&
      /^[\w.-]{1,64}$/u.test(value.key) &&
      'request' in value
    )
      return {
        id: null,
        submission: { key: value.key, request: parseRequest(value.request) },
      };
  } catch {
    /* Missing or unavailable session storage cannot fabricate a job. */
  }
  return { id: null, submission: null };
}
const message = (error: unknown) =>
  error instanceof ApiError
    ? error.message
    : 'The request failed. Retry to recover its status.';

/**
 * Own submission recovery, one nonoverlapping polling chain, and cancellation.
 *
 * Accepted jobs and unconfirmed idempotent submissions survive same-tab reloads
 * through session storage. Polls stop at terminal states, after five consecutive
 * failures, after 30 minutes, or on unmount. Pausing polling never cancels server
 * work. Cancellation remains a request until the API reports a terminal state.
 *
 * @returns Job/actions and separate submission, transport, recovery, and cooldown states.
 */
export function useJob() {
  const recovery = useMemo(() => recover(), []);
  const [id, setId] = useState(recovery.id);
  const [job, setJob] = useState<Job | null>(null);
  const [pending, setPending] = useState(recovery.submission);
  const pendingRef = useRef(pending);
  const [error, setError] = useState('');
  const [verifiedAt, setVerifiedAt] = useState(0);
  const [storageWarning, setStorageWarning] = useState('');
  const [busy, setBusy] = useState(false);
  const [retryAt, setRetryAt] = useState(0);
  const [polling, setPolling] = useState(Boolean(recovery.id));
  const [revision, setRevision] = useState(0);
  const action = useRef<AbortController | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    if (retryAt === 0) return;
    const timer = setTimeout(
      () => setRetryAt(0),
      Math.max(0, retryAt - Date.now()),
    );
    return () => clearTimeout(timer);
  }, [retryAt]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      action.current?.abort();
    };
  }, []);

  function persist(value: Submission | { id: string }) {
    try {
      sessionStorage.setItem(storageKey, JSON.stringify(value));
    } catch {
      setStorageWarning(
        'Session recovery is unavailable. Keep this tab open until the experiment finishes.',
      );
    }
  }

  useEffect(() => {
    if (!id || !polling) return;
    const controller = new AbortController();
    const deadline = Date.now() + 30 * 60 * 1000;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    async function poll() {
      let wait = 1000;
      try {
        const current = await getJob(id ?? '', controller.signal);
        if (controller.signal.aborted) return;
        setJob((previous) => latestSnapshot(previous, current));
        setVerifiedAt(performance.now());
        setError('');
        failures = 0;
        if (isTerminal(current.state)) {
          setPolling(false);
          return;
        }
      } catch (failure) {
        if (controller.signal.aborted) return;
        setVerifiedAt(0);
        failures += 1;
        setError(message(failure));
        if (failure instanceof ApiError && failure.status === 404) {
          setPolling(false);
          setId(null);
          setJob(null);
          return;
        }
        if (failures >= 5) {
          setPolling(false);
          return;
        }
        wait = Math.max(
          Math.min(1000 * 2 ** failures, 30000),
          failure instanceof ApiError ? failure.retryAfter : 1000,
        );
      }
      if (Date.now() >= deadline) {
        setError(
          'Status polling paused after 30 minutes. The server may still be running; resume to check.',
        );
        setPolling(false);
        return;
      }
      timer = setTimeout(() => {
        poll().catch(() => setError('Unable to poll this experiment.'));
      }, wait);
    }
    poll().catch(() => setError('Unable to poll this experiment.'));
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [id, polling, revision]);

  /** Preserve ambiguous acceptance; only explicit client rejections release the key. */
  function recordSubmissionFailure(failure: unknown) {
    setVerifiedAt(0);
    setError(message(failure));
    if (!(failure instanceof ApiError) || failure.status < 400) return;
    setRetryAt(Date.now() + failure.retryAfter);
    if (failure.status >= 500) return;
    pendingRef.current = null;
    setPending(null);
    try {
      sessionStorage.removeItem(storageKey);
    } catch {
      setStorageWarning('Session recovery is unavailable.');
    }
  }

  async function submit(request: RunRequest) {
    if (
      action.current ||
      (job && !isTerminal(job.state)) ||
      (id && !job) ||
      Date.now() < retryAt
    )
      return;
    const controller = new AbortController();
    action.current = controller;
    const submission = pendingRef.current ?? {
      key: crypto.randomUUID(),
      request,
    };
    pendingRef.current = submission;
    setPending(submission);
    persist(submission);
    setBusy(true);
    setError('');
    setVerifiedAt(0);
    try {
      const accepted = await startJob(
        submission.request,
        submission.key,
        controller.signal,
      );
      if (!mounted.current) return;
      setJob(accepted);
      setVerifiedAt(performance.now());
      setId(accepted.id);
      persist({ id: accepted.id });
      pendingRef.current = null;
      setPending(null);
      setPolling(!isTerminal(accepted.state));
      setRevision((value) => value + 1);
    } catch (failure) {
      if (!mounted.current) return;
      recordSubmissionFailure(failure);
    } finally {
      action.current = null;
      if (mounted.current) setBusy(false);
    }
  }

  async function cancel() {
    if (!job || isTerminal(job.state) || action.current) return;
    const controller = new AbortController();
    action.current = controller;
    setBusy(true);
    setVerifiedAt(0);
    try {
      const current = await cancelJob(job.id, controller.signal);
      if (!mounted.current) return;
      setJob((previous) => latestSnapshot(previous, current));
      setVerifiedAt(performance.now());
      if (isTerminal(current.state)) setPolling(false);
      setError('');
    } catch (failure) {
      if (mounted.current) {
        setVerifiedAt(0);
        setError(message(failure));
      }
    } finally {
      action.current = null;
      if (mounted.current) setBusy(false);
    }
  }

  function resume() {
    setVerifiedAt(0);
    setError('');
    setPolling(true);
    setRevision((value) => value + 1);
  }
  return {
    job,
    pending,
    busy,
    error,
    verifiedAt,
    storageWarning,
    polling,
    submit,
    cancel,
    resume,
    recovering: Boolean(id && !job),
    coolingDown: retryAt > 0,
  };
}

/** Retains a newer terminal/progress/cancellation acknowledgement over an older reply. */
function latestSnapshot(previous: Job | null, current: Job): Job {
  if (previous?.id !== current.id) return current;
  if (
    isTerminal(previous.state) ||
    previous.completed_batches > current.completed_batches
  )
    return previous;
  if (
    previous.cancel_requested &&
    !current.cancel_requested &&
    !isTerminal(current.state)
  )
    return previous;
  return current;
}
