import { useEffect, useState } from 'react';
import { isTerminal, type Dataset, type WorkerCount } from '@/api/contracts';
import { useNavigation } from '@/components/useNavigation';
import { useCatalog } from '@/features/datasets/hooks/useCatalog';
import { useHistory } from '@/features/history/hooks/useHistory';
import { useJob } from '@/features/benchmarks/hooks/useJob';
import { useApiHealth } from '@/features/diagnostics/hooks/useApiHealth';

type Execution = ReturnType<typeof useJob>;

function executionState({ job, recovering, busy, pending }: Execution) {
  const running = job !== null && !isTerminal(job.state);
  const active = recovering || running;
  return {
    running,
    active,
    terminalId: job && !running ? job.id : null,
    configurationLocked: active || busy || Boolean(pending),
  };
}

function isJobVerified(
  execution: Execution,
  health: ReturnType<typeof useApiHealth>,
) {
  return (
    health.status === 'API online' &&
    !health.checking &&
    !execution.error &&
    execution.verifiedAt > health.failedAt
  );
}

function selectedDatasetId(
  datasets: readonly Dataset[],
  selected: string,
  pending: Execution['pending'],
) {
  if (pending) return pending.request.dataset_id;
  if (selected) return selected;
  return (
    datasets.find(
      (dataset) =>
        dataset.error_code === null && (dataset.input_count ?? 0) > 0,
    )?.dataset_id ?? ''
  );
}

function backToTop() {
  document.getElementById('main-content')?.focus({ preventScroll: true });
  window.scrollTo({
    top: 0,
    behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches
      ? 'auto'
      : 'smooth',
  });
}

export function useWorkspace() {
  const { view, navigate } = useNavigation();
  const catalog = useCatalog();
  const execution = useJob();
  const state = executionState(execution);
  const history = useHistory(state.terminalId);
  const health = useApiHealth(
    Math.max(catalog.verifiedAt, history.verifiedAt, execution.verifiedAt),
  );
  const checkHealth = health.check;
  useEffect(() => {
    void checkHealth();
  }, [checkHealth]);
  const [selected, setSelected] = useState<WorkerCount>(4);
  const [datasetId, setDatasetId] = useState('');
  const selectedDataset = selectedDatasetId(
    catalog.datasets,
    datasetId,
    execution.pending,
  );

  useEffect(() => {
    if (view === 'history') document.getElementById('history-search')?.focus();
  }, [view]);

  const jobVerified = isJobVerified(execution, health);

  return {
    view,
    navigate,
    catalog,
    execution,
    history,
    health,
    selected,
    selectWorker: setSelected,
    datasetId: selectedDataset,
    selectDataset: setDatasetId,
    active: state.active,
    configurationLocked: state.configurationLocked,
    jobVerified,
    confirmedActive: jobVerified && state.running,
    backToTop,
  };
}
