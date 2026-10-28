import type { View } from '@/components/useNavigation';

import type { useCatalog } from './useHistory';
import type { useJob } from './useJob';

interface Props {
  readonly catalog: ReturnType<typeof useCatalog>;
  readonly historyError: string;
  readonly retryHistory: () => void;
  readonly execution: ReturnType<typeof useJob>;
  readonly active: boolean;
  readonly view: View;
}

export default function BenchmarkNotices({
  catalog,
  historyError,
  retryHistory,
  execution,
  active,
  view,
}: Props) {
  return (
    <>
      {catalog.error && (
        <div className="notice" role="alert">
          {catalog.error}{' '}
          <button type="button" onClick={catalog.reload}>
            Retry datasets
          </button>
        </div>
      )}
      {historyError && (
        <div className="notice" role="alert">
          {historyError}{' '}
          <button type="button" onClick={retryHistory}>
            Retry history
          </button>
        </div>
      )}
      <JobNotices execution={execution} active={active} />
      <DatasetAvailability catalog={catalog} />
      {active && view !== 'experiments' && (
        <output className="notice">
          {execution.job?.state ?? 'Recovering experiment'} ·{' '}
          <a href="#experiments">View job progress</a>
        </output>
      )}
    </>
  );
}

/** Keep transport recovery distinct from acceptance and durable job state. */
function JobNotices({
  execution,
  active,
}: Pick<Props, 'execution' | 'active'>) {
  return (
    <>
      {execution.error && (
        <div className="notice" role="alert">
          {execution.error}
          {!execution.polling && (active || execution.job) && (
            <button type="button" onClick={execution.resume}>
              Resume status polling
            </button>
          )}
        </div>
      )}
      {execution.storageWarning && (
        <p className="notice" role="alert">
          {execution.storageWarning}
        </p>
      )}
      {execution.pending && (
        <p className="notice">
          Submission acceptance is unconfirmed. Retry the same submission to
          recover it safely; its idempotency key is retained.
        </p>
      )}
    </>
  );
}

function DatasetAvailability({ catalog }: Pick<Props, 'catalog'>) {
  if (catalog.loading) return <output>Loading registered datasets…</output>;
  if (catalog.error || catalog.datasets.length !== 0) return null;

  return (
    <p className="notice">
      No datasets registered. Configure TACHYRON_DATASETS on the local server,
      then restart it and retry datasets.{' '}
      <button type="button" onClick={catalog.reload}>
        Retry datasets
      </button>
    </p>
  );
}
