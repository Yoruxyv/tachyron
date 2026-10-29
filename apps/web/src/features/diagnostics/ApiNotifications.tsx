import {
  useNotifications,
  type NotificationSources,
} from './hooks/useNotifications';
import { useLayoutEffect, useState } from 'react';
import { createPortal } from 'react-dom';

/** One stable toast per failure episode; request owners retain errors and recovery state. */
export default function ApiNotifications({
  importDialogOpen = false,
  ...sources
}: NotificationSources & { readonly importDialogOpen?: boolean }) {
  const [target, setTarget] = useState<HTMLElement | null>(null);
  useLayoutEffect(() => {
    setTarget(
      importDialogOpen ? document.getElementById('import-notifications') : null,
    );
  }, [importDialogOpen]);
  const {
    notice,
    announcement,
    dismiss,
    retry,
    retryDisabled,
    checking,
    canRetry,
  } = useNotifications(sources);
  return createPortal(
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
    </>,
    target ?? document.body,
  );
}
