import {
  useNotifications,
  type NotificationSources,
} from './hooks/useNotifications';

/** One stable toast per failure episode; request owners retain errors and recovery state. */
export default function ApiNotifications(sources: NotificationSources) {
  const {
    notice,
    announcement,
    dismiss,
    retry,
    retryDisabled,
    checking,
    canRetry,
  } = useNotifications(sources);
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
              {canRetry && (
                <button
                  type="button"
                  className="primary toast-retry"
                  aria-disabled={retryDisabled}
                  aria-busy={checking}
                  onClick={retry}
                >
                  {checking ? 'Checking…' : 'Retry'}
                </button>
              )}
            </div>
          </div>
        </section>
      )}
    </>
  );
}
