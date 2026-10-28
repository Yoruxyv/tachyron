import { useEffect, useState } from 'react';

import { isTerminal, type WorkerCount } from '@/api/contracts';
import Shell from '@/components/Shell';
import { useNavigation } from '@/components/useNavigation';
import BenchmarkNotices from '@/features/benchmarks/BenchmarkNotices';
import BenchmarkWorkspace from '@/features/benchmarks/BenchmarkWorkspace';
import { useCatalog, useHistory } from '@/features/benchmarks/useHistory';
import { useJob } from '@/features/benchmarks/useJob';
import { useApiHealth } from '@/features/benchmarks/useApiHealth';
import WorkspaceStatus from '@/features/benchmarks/WorkspaceStatus';
import ApiNotifications from '@/features/benchmarks/ApiNotifications';

export default function App() {
  const { view, navigate } = useNavigation();
  const catalog = useCatalog();
  const execution = useJob();
  const terminalId =
    execution.job && isTerminal(execution.job.state) ? execution.job.id : null;
  const history = useHistory(terminalId);
  const health = useApiHealth(
    Math.max(catalog.verifiedAt, history.verifiedAt, execution.verifiedAt),
  );
  const checkHealth = health.check;
  useEffect(() => {
    void checkHealth();
  }, [checkHealth]);
  const [selected, setSelected] = useState<WorkerCount>(4);
  const [datasetId, setDatasetId] = useState('');
  const selectedDataset =
    execution.pending?.request.dataset_id ??
    (datasetId ||
      catalog.datasets.find(
        (dataset) =>
          dataset.error_code === null && (dataset.input_count ?? 0) > 0,
      )?.dataset_id ||
      '');

  useEffect(() => {
    if (view === 'history') document.getElementById('history-search')?.focus();
  }, [view]);

  const active =
    execution.recovering ||
    Boolean(execution.job && !isTerminal(execution.job.state));
  const jobVerified =
    health.status === 'API online' &&
    !health.checking &&
    !execution.error &&
    execution.verifiedAt > health.failedAt;
  const confirmedActive =
    jobVerified && Boolean(execution.job && !isTerminal(execution.job.state));

  return (
    <Shell
      view={view}
      navigate={navigate}
      active={confirmedActive}
      datasets={catalog.datasets}
      datasetId={selectedDataset}
      selectDataset={setDatasetId}
      configurationLocked={
        active || execution.busy || Boolean(execution.pending)
      }
      workspaceStatus={<WorkspaceStatus health={health} />}
    >
      <div className="stack">
        <ApiNotifications
          health={health}
          catalog={catalog}
          history={history}
          execution={execution}
        />
        <BenchmarkNotices
          catalog={catalog}
          execution={execution}
          active={active}
          view={view}
          jobVerified={jobVerified}
        />
        <BenchmarkWorkspace
          view={view}
          navigate={navigate}
          datasets={catalog.datasets}
          datasetError={catalog.error}
          retryDatasets={catalog.reload}
          execution={execution}
          active={active}
          history={history}
          selected={selected}
          select={setSelected}
          datasetId={selectedDataset}
          selectDataset={setDatasetId}
          health={health}
          jobVerified={jobVerified}
        />
        <p className="methodology">
          <span>
            ◉ &nbsp; Single-host process-pool measurements. Independent
            experiments are never pooled into one scaling curve. Unavailable
            metrics are not zero.
          </span>
          <button
            type="button"
            className="back-to-top"
            onClick={() => {
              document
                .getElementById('main-content')
                ?.focus({ preventScroll: true });
              window.scrollTo({
                top: 0,
                behavior: window.matchMedia('(prefers-reduced-motion: reduce)')
                  .matches
                  ? 'auto'
                  : 'smooth',
              });
            }}
          >
            Back to top ↑
          </button>
        </p>
      </div>
    </Shell>
  );
}
