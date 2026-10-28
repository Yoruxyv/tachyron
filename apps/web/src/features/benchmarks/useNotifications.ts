import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';

import { isTerminal } from '@/api/contracts';

import type { useApiHealth } from './useApiHealth';
import type { useCatalog, useHistory } from './useHistory';
import type { useJob } from './useJob';

export interface NotificationSources {
  readonly health: ReturnType<typeof useApiHealth>;
  readonly catalog: ReturnType<typeof useCatalog>;
  readonly history: ReturnType<typeof useHistory>;
  readonly execution: ReturnType<typeof useJob>;
}
type Toast = {
  readonly kind: 'failure' | 'recovery';
  readonly title: string;
  readonly message: string;
  readonly retry: boolean;
};

function hasRetainedWork(execution: NotificationSources['execution']) {
  return Boolean(
    execution.pending ||
    execution.recovering ||
    (execution.job && !isTerminal(execution.job.state)),
  );
}

function requestsAreSettling({
  health,
  catalog,
  history,
  execution,
}: NotificationSources) {
  const verifyingJob =
    execution.polling && !execution.error && execution.verifiedAt === 0;
  return (
    health.checking ||
    catalog.loading ||
    history.loading ||
    history.detailLoading ||
    execution.busy ||
    verifyingJob
  );
}

function notificationAvailability({
  health,
  catalog,
  history,
  execution,
}: NotificationSources) {
  const offline = health.status.startsWith('API unavailable');
  const requestFailure = Boolean(
    catalog.error || history.error || history.detailError,
  );
  return {
    offline,
    healthy: health.status === 'API online' && !health.checking,
    requestFailure,
    meaningfulRetry:
      offline ||
      requestFailure ||
      Boolean(execution.error && hasRetainedWork(execution)),
  };
}
type Availability = ReturnType<typeof notificationAvailability>;

function failureToast(
  { catalog, history, execution }: NotificationSources,
  state: Availability,
): Toast {
  if (state.offline) {
    return {
      kind: 'failure',
      title: 'Unable to reach the local API',
      message:
        catalog.error && history.error
          ? 'Datasets and experiment history are temporarily unavailable.'
          : 'Check that the local backend is running, then retry the connection.',
      retry: state.meaningfulRetry,
    };
  }
  return {
    kind: 'failure',
    title: 'API requests need attention',
    message:
      execution.error && !state.requestFailure
        ? execution.error
        : 'Some requests failed. Review the affected controls for recovery options.',
    retry: state.meaningfulRetry,
  };
}

function returnFocusToStatus() {
  if (document.activeElement?.closest('.api-toast'))
    document
      .querySelector<HTMLButtonElement>('.workspace-connection')
      ?.focus({ preventScroll: true });
}

/** Own notification episodes independently of retry requests and their completion. */
function useNotificationEpisode(
  props: NotificationSources,
  availability: Availability,
  settling: boolean,
) {
  const [notice, setNotice] = useState<Toast | null>(null);
  const [announcement, setAnnouncement] = useState('');
  const episode = useRef(false);
  const wasOffline = useRef(false);
  const recovered = useRef(false);
  const jobFailure = useRef<number | null>(null);
  const recoveryFocus = useRef(false);
  const { offline, healthy, requestFailure, meaningfulRetry } = availability;
  const { error: jobError, verifiedAt: jobVerifiedAt } = props.execution;
  const hasFailure = offline || requestFailure || Boolean(jobError);
  const { status, check } = props.health;
  const { title, message } = failureToast(props, availability);

  const showRecovery = useCallback((title: string, message: string) => {
    recoveryFocus.current =
      document.activeElement?.classList.contains('toast-retry') ?? false;
    setNotice({ kind: 'recovery', title, message, retry: false });
  }, []);
  useLayoutEffect(() => {
    if (notice?.kind !== 'recovery' || !recoveryFocus.current) return;
    recoveryFocus.current = false;
    document
      .querySelector<HTMLButtonElement>('.toast-dismiss')
      ?.focus({ preventScroll: true });
  }, [notice]);

  useEffect(() => {
    if (offline) wasOffline.current = true;
    if (jobError) jobFailure.current = jobVerifiedAt;
    if (hasFailure && !episode.current && status !== 'Not checked') {
      episode.current = true;
      recovered.current = false;
      setNotice({ kind: 'failure', title, message, retry: meaningfulRetry });
      setAnnouncement('An API request failed. Recovery actions are available.');
      if (!offline) void check();
    }
    if (wasOffline.current && healthy) {
      wasOffline.current = false;
      recovered.current = true;
      showRecovery('Connection restored', 'The local API is available again.');
      setAnnouncement(
        'API connection restored. Saved jobs still require their own status verification.',
      );
    }
    const jobRecovered =
      jobFailure.current === null || jobVerifiedAt > jobFailure.current;
    if (hasFailure || !healthy || settling || !jobRecovered || !episode.current)
      return;

    const announceRecovery = !recovered.current;
    episode.current = false;
    jobFailure.current = null;
    recovered.current = false;
    if (!announceRecovery) return;
    showRecovery(
      'Requests available',
      'Previously failed requests completed successfully.',
    );
    setAnnouncement('API requests are available again.');
  }, [
    offline,
    healthy,
    hasFailure,
    settling,
    jobError,
    jobVerifiedAt,
    check,
    title,
    message,
    status,
    showRecovery,
    meaningfulRetry,
  ]);
  useEffect(
    () => () => {
      episode.current = false;
    },
    [],
  );

  const dismiss = useCallback(() => {
    returnFocusToStatus();
    setNotice(null);
    setAnnouncement('');
  }, []);
  useEffect(() => {
    if (notice?.kind !== 'recovery') return;
    const timer = setTimeout(dismiss, 5000);
    return () => clearTimeout(timer);
  }, [notice, dismiss]);
  return { notice, announcement, dismiss };
}

function refreshStaleResources({
  catalog,
  history,
  health,
}: NotificationSources) {
  let requested = false;
  if (catalog.error || catalog.verifiedAt <= health.failedAt) {
    catalog.reload();
    requested = true;
  }
  if (
    history.error ||
    history.detailError ||
    history.verifiedAt <= health.failedAt
  ) {
    history.reload();
    requested = true;
  }
  return requested;
}

async function retryRetainedWork({ execution, health }: NotificationSources) {
  if (execution.busy || execution.coolingDown || !hasRetainedWork(execution))
    return false;
  const stale =
    execution.recovering ||
    Boolean(
      execution.job &&
      !isTerminal(execution.job.state) &&
      execution.verifiedAt <= health.failedAt,
    );
  if (!execution.error && !stale) return false;
  if (execution.pending) {
    await execution.submit(execution.pending.request);
    return false;
  }
  execution.resume();
  return true;
}

/** Coordinate one health probe followed by the existing resource/job request owners. */
function useConnectionRetry(
  props: NotificationSources,
  availability: Availability,
  settling: boolean,
) {
  const [phase, setPhase] = useState<'idle' | 'checking' | 'refreshing'>(
    'idle',
  );
  const retryInFlight = useRef(false);
  const mounted = useRef(true);
  const { offline, requestFailure } = availability;
  const retrying = phase !== 'idle';
  const disabled =
    retrying ||
    settling ||
    (!offline && !requestFailure && props.execution.coolingDown);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const finish = useCallback(() => {
    retryInFlight.current = false;
    if (mounted.current) setPhase('idle');
  }, []);
  useEffect(() => {
    if (phase === 'refreshing' && !settling) finish();
  }, [phase, settling, finish]);

  async function retry() {
    if (disabled || retryInFlight.current) return;
    retryInFlight.current = true;
    setPhase('checking');
    let refreshRequested = false;
    try {
      if (!(await props.health.check()) || !mounted.current) return;
      refreshRequested = refreshStaleResources(props);
      const resuming = await retryRetainedWork(props);
      refreshRequested = refreshRequested || resuming;
    } finally {
      if (!refreshRequested) finish();
      else if (mounted.current) setPhase('refreshing');
    }
  }
  return { retry, retrying, disabled };
}

/** Present one notification with its existing request owners and explicit retry phase. */
export function useNotifications(sources: NotificationSources) {
  const availability = notificationAvailability(sources);
  const settling = requestsAreSettling(sources);
  const { notice, announcement, dismiss } = useNotificationEpisode(
    sources,
    availability,
    settling,
  );
  const { retry, retrying, disabled } = useConnectionRetry(
    sources,
    availability,
    settling,
  );
  return {
    notice,
    announcement,
    dismiss,
    retry,
    retryDisabled: disabled,
    checking: retrying || settling,
    canRetry:
      notice?.kind === 'failure' &&
      (notice.retry || availability.meaningfulRetry),
  };
}
