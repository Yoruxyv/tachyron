import type { View } from '@/components/useNavigation';

import type { useCatalog } from '@/features/datasets/hooks/useCatalog';
import type { useJob } from '@/features/benchmarks/hooks/useJob';

interface Props {
  readonly catalog: ReturnType<typeof useCatalog>;
  readonly execution: ReturnType<typeof useJob>;
  readonly active: boolean;
  readonly view: View;
  readonly jobVerified: boolean;
}

export default function BenchmarkNotices({
  catalog,
  execution,
  active,
  view,
  jobVerified,
}: Props) {
  let jobStatus = execution.error;
  if (active)
    jobStatus = jobVerified
      ? (execution.job?.state ?? '')
      : 'Saved experiment status unverified';
  return (
    <>
      <JobNotices execution={execution} view={view} />
      <DatasetAvailability catalog={catalog} />
      {(active || execution.error) &&
        !execution.pending &&
        view !== 'experiments' && (
          <output className="request-error">
            {jobStatus}
            {' · '}
            <a href="#experiments">
              {active ? 'View job progress' : 'Review experiment'}
            </a>
          </output>
        )}
    </>
  );
}

/** Keep transport recovery distinct from acceptance and durable job state. */
function JobNotices({ execution, view }: Pick<Props, 'execution' | 'view'>) {
  return (
    <>
      {execution.storageWarning && (
        <p className="notice" role="alert">
          {execution.storageWarning}
        </p>
      )}
      {execution.pending && view !== 'experiments' && (
        <p className="request-error">
          Submission acceptance is unconfirmed.{' '}
          <a href="#experiments">Review submission</a>
        </p>
      )}
    </>
  );
}

function DatasetAvailability({ catalog }: Pick<Props, 'catalog'>) {
  if (catalog.loading && !catalog.error && catalog.verifiedAt === 0)
    return <output>Loading registered datasets…</output>;
  if (catalog.error || catalog.datasets.length !== 0) return null;

  return (
    <p className="notice">
      No datasets registered. Configure TACHYRON_DATASETS on the local server,
      then restart it and retry datasets.{' '}
      <button type="button" onClick={catalog.reload} disabled={catalog.loading}>
        Retry datasets
      </button>
    </p>
  );
}
