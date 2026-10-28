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

interface Props {
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

function returnFocusToStatus() {
  if (document.activeElement?.closest('.api-toast'))
    document
      .querySelector<HTMLButtonElement>('.workspace-connection')
      ?.focus({ preventScroll: true });
}

/** One stable toast per failure episode; request owners retain errors and recovery state. */
export default function ApiNotifications({
  health,
  catalog,
  history,
  execution,
}: Props) {
  const [notice, setNotice] = useState<Toast | null>(null);
  const [announcement, setAnnouncement] = useState('');
  const [retrying, setRetrying] = useState(false);
  const episode = useRef(false);
  const wasOffline = useRef(false);
  const recovered = useRef(false);
  const jobFailure = useRef<number | null>(null);
  const retryInFlight = useRef(false);
  const awaitingRefresh = useRef(false);
  const mounted = useRef(true);
  const recoveryFocus = useRef(false);
  const offline = health.status.startsWith('API unavailable');
  const healthy = health.status === 'API online' && !health.checking;
  const jobError = execution.error;
  const jobVerifiedAt = execution.verifiedAt;
  const requestFailure = Boolean(
    catalog.error || history.error || history.detailError,
  );
  const hasFailure = offline || requestFailure || Boolean(jobError);
  const verifyingJob = execution.polling && !jobError && jobVerifiedAt === 0;
  const settling =
    health.checking ||
    catalog.loading ||
    history.loading ||
    history.detailLoading ||
    execution.busy ||
    verifyingJob;
  const check = health.check;
  const retained = Boolean(
    execution.pending ||
    execution.recovering ||
    (execution.job && !isTerminal(execution.job.state)),
  );
  const meaningfulRetry =
    offline || requestFailure || Boolean(jobError && retained);
  const retryDisabled =
    retrying ||
    settling ||
    (!offline && !requestFailure && execution.coolingDown);
  let failureTitle = 'API requests need attention';
  let failureMessage =
    'Some requests failed. Review the affected controls for recovery options.';
  if (jobError && !requestFailure) failureMessage = jobError;
  if (offline) {
    failureTitle = 'Unable to reach the local API';
    failureMessage =
      'Check that the local backend is running, then retry the connection.';
    if (catalog.error && history.error)
      failureMessage =
        'Datasets and experiment history are temporarily unavailable.';
  }
  const showRecovery = useCallback((title: string, message: string) => {
    recoveryFocus.current =
      document.activeElement?.classList.contains('toast-retry') ?? false;
    setNotice({ kind: 'recovery', title, message, retry: false });
  }, []);
  useLayoutEffect(() => {
    if (notice?.kind === 'recovery' && recoveryFocus.current) {
      recoveryFocus.current = false;
      document
        .querySelector<HTMLButtonElement>('.toast-dismiss')
        ?.focus({ preventScroll: true });
    }
  }, [notice]);

  useEffect(() => {
    if (offline) wasOffline.current = true;
    if (jobError) jobFailure.current = jobVerifiedAt;
    if (hasFailure && !episode.current && health.status !== 'Not checked') {
      episode.current = true;
      recovered.current = false;
      setNotice({
        kind: 'failure',
        title: failureTitle,
        message: failureMessage,
        retry: meaningfulRetry,
      });
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
    if (
      !hasFailure &&
      healthy &&
      !settling &&
      jobRecovered &&
      episode.current
    ) {
      if (!recovered.current) {
        showRecovery(
          'Requests available',
          'Previously failed requests completed successfully.',
        );
        setAnnouncement('API requests are available again.');
      }
      episode.current = false;
      jobFailure.current = null;
      recovered.current = false;
    }
  }, [
    offline,
    healthy,
    hasFailure,
    settling,
    jobError,
    jobVerifiedAt,
    check,
    failureTitle,
    failureMessage,
    health.status,
    showRecovery,
    meaningfulRetry,
  ]);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      episode.current = false;
    };
  }, []);
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
  const finishRetry = useCallback(() => {
    retryInFlight.current = false;
    awaitingRefresh.current = false;
    if (mounted.current) setRetrying(false);
  }, []);
  useEffect(() => {
    if (awaitingRefresh.current && !settling) finishRetry();
  }, [settling, finishRetry]);

  async function retry() {
    if (retryDisabled || retryInFlight.current) return;
    retryInFlight.current = true;
    setRetrying(true);
    let refreshRequested = false;
    try {
      if (!(await check()) || !mounted.current) return;
      if (catalog.error || catalog.verifiedAt <= health.failedAt) {
        catalog.reload();
        refreshRequested = true;
      }
      if (
        history.error ||
        history.detailError ||
        history.verifiedAt <= health.failedAt
      ) {
        history.reload();
        refreshRequested = true;
      }
      const staleJob =
        execution.recovering ||
        Boolean(
          execution.job &&
          !isTerminal(execution.job.state) &&
          execution.verifiedAt <= health.failedAt,
        );
      if (
        (jobError || staleJob) &&
        retained &&
        !execution.busy &&
        !execution.coolingDown
      ) {
        if (execution.pending)
          await execution.submit(execution.pending.request);
        else {
          execution.resume();
          refreshRequested = true;
        }
      }
      awaitingRefresh.current = refreshRequested;
    } finally {
      if (!refreshRequested) finishRetry();
    }
  }

  return (
    <>
      <output className="sr-only" aria-live="polite" aria-atomic="true">
        {announcement}
      </output>
      {notice && (
        <section className="api-notifications" aria-label="API notification">
          <div className="api-toast">
            <h2>{notice.title}</h2>
            <p>{notice.message}</p>
            <div className="toast-actions">
              <button type="button" className="toast-dismiss" onClick={dismiss}>
                Dismiss
              </button>
              {notice.kind === 'failure' &&
                (notice.retry || meaningfulRetry) && (
                  <button
                    type="button"
                    className="primary toast-retry"
                    aria-disabled={retryDisabled}
                    aria-busy={retrying || settling}
                    onClick={retry}
                  >
                    {retrying || settling ? 'Checking…' : 'Retry'}
                  </button>
                )}
            </div>
          </div>
        </section>
      )}
    </>
  );
}
