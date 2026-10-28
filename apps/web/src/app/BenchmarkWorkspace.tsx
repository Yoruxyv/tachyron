import { isTerminal, type Dataset, type WorkerCount } from '@/api/contracts';
import type { ReactNode } from 'react';
import type { View } from '@/components/useNavigation';

import ApiStatus from '@/features/diagnostics/ApiStatus';
import Comparison from '@/features/history/Comparison';
import History from '@/features/history/History';
import JobForm from '@/features/benchmarks/experiments/JobForm';
import JobProgress from '@/features/benchmarks/experiments/JobProgress';
import Results, { Kpis } from '@/features/benchmarks/results/Results';
import type { useHistory } from '@/features/history/hooks/useHistory';
import type { useJob } from '@/features/benchmarks/hooks/useJob';
import type { useApiHealth } from '@/features/diagnostics/hooks/useApiHealth';

interface Props {
  readonly importAction?: ReactNode;
  readonly importPanel?: ReactNode;
  readonly importing?: boolean;
  readonly view: View;
  readonly navigate: (view: View) => void;
  readonly datasets: Dataset[];
  readonly datasetError: string;
  readonly retryDatasets: () => void;
  readonly execution: ReturnType<typeof useJob>;
  readonly active: boolean;
  readonly history: ReturnType<typeof useHistory>;
  readonly selected: WorkerCount;
  readonly select: (workers: WorkerCount) => void;
  readonly datasetId: string;
  readonly selectDataset: (id: string) => void;
  readonly health: ReturnType<typeof useApiHealth>;
  readonly jobVerified: boolean;
}

/**
 * Compose view content without owning requests or persistent selection.
 * App keeps lifecycle hooks and worker inspection mounted across navigation.
 */
export default function BenchmarkWorkspace({
  view,
  navigate,
  datasets,
  datasetError,
  retryDatasets,
  execution,
  active,
  history,
  selected,
  select,
  datasetId,
  selectDataset,
  health,
  jobVerified,
  importAction,
  importPanel,
  importing = false,
}: Props) {
  const form = (
    <JobForm
      datasets={datasets}
      datasetError={datasetError}
      retryDatasets={retryDatasets}
      disabled={execution.busy || active || execution.coolingDown || importing}
      importAction={view === 'experiments' ? importAction : undefined}
      pending={execution.pending?.request}
      submit={execution.submit}
      datasetId={datasetId}
      selectDataset={selectDataset}
      compact={view === 'overview'}
      selectedWorker={selected}
      selectWorker={select}
    />
  );
  const records = (
    <History
      items={history.items}
      selectedId={history.selectedId}
      select={(id) => {
        history.select(id);
        if (view === 'overview') navigate('compare');
      }}
      loading={history.loading}
      initialLoading={history.initialLoading}
      offset={history.offset}
      setOffset={history.setOffset}
      compact={view === 'overview'}
      worker={selected}
      error={history.error}
      retry={history.reload}
    />
  );

  if (view === 'overview')
    return (
      <>
        <Kpis detail={history.detail} selected={selected} />
        <Results
          detail={history.detail}
          selected={selected}
          select={select}
          overview
        />
        <div className="overview-bottom">
          {records}
          {form}
        </div>
      </>
    );

  if (view === 'experiments')
    return (
      <div className="experiment-grid">
        <div className="stack">
          {form}
          {importPanel}
        </div>
        <JobProgress
          job={execution.job}
          busy={execution.busy}
          cancel={execution.cancel}
          verified={jobVerified}
          recovering={execution.recovering}
          pending={Boolean(execution.pending)}
          error={execution.error}
          retryStatus={
            execution.recovering ||
            (execution.job && !isTerminal(execution.job.state))
              ? execution.resume
              : undefined
          }
        />
      </div>
    );

  if (view === 'api') return <ApiStatus health={health} />;

  return (
    <Comparison
      history={history}
      selected={selected}
      select={select}
      historyView={view === 'history'}
    >
      {view === 'history' && records}
    </Comparison>
  );
}
