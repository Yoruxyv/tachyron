import { useState, type ReactNode } from 'react';
import { isTerminal, type Dataset } from '@/api/contracts';
import type { View } from '@/components/useNavigation';

import ApiStatus from '@/features/diagnostics/ApiStatus';
import Comparison from '@/features/history/Comparison';
import AllWorkerResults from '@/features/history/AllWorkerResults';
import {
  inspectedCount,
  measuredCounts,
} from '@/features/benchmarks/results/chartData';
import History from '@/features/history/History';
import JobForm from '@/features/benchmarks/experiments/JobForm';
import JobProgress from '@/features/benchmarks/experiments/JobProgress';
import Results, { Kpis } from '@/features/benchmarks/results/Results';
import type { useHistory } from '@/features/history/hooks/useHistory';
import type { useJob } from '@/features/benchmarks/hooks/useJob';
import type { useApiHealth } from '@/features/diagnostics/hooks/useApiHealth';

import LiveWorkerMonitor from '@/features/operations/LiveWorkerMonitor';
import {
  OperationStatus,
  type ExecutionMode,
} from '@/features/operations/OperationControls';
import { useOperationalExecution } from '@/features/operations/useOperationalExecution';

interface Props {
  readonly importAction?: ReactNode;
  readonly importing?: boolean;
  readonly view: View;
  readonly workerResults: boolean;
  readonly comparisonId: string | null;
  readonly navigate: (view: View) => void;
  readonly datasets: Dataset[];
  readonly datasetError: string;
  readonly retryDatasets: () => void;
  readonly execution: ReturnType<typeof useJob>;
  readonly active: boolean;
  readonly history: ReturnType<typeof useHistory>;
  readonly selected: number;
  readonly select: (workers: number) => void;
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
  workerResults,
  comparisonId,
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
  importing = false,
}: Props) {
  const operations = useOperationalExecution();
  const [mode, setMode] = useState<ExecutionMode>(
    operations.mode ?? 'benchmark',
  );
  const formMode = execution.pending || active ? 'benchmark' : mode;
  const inspectionCounts = measuredCounts(
    history.detail?.result?.summaries ?? [],
  );
  const form = (
    <JobForm
      datasets={datasets}
      datasetError={datasetError}
      retryDatasets={retryDatasets}
      disabled={
        execution.busy ||
        active ||
        execution.coolingDown ||
        importing ||
        (view === 'experiments' && (operations.sending || operations.running))
      }
      {...(view === 'experiments' && {
        controls: {
          value: formMode,
          select: setMode,
          execution: operations,
        },
      })}
      importAction={view === 'experiments' ? importAction : undefined}
      pending={execution.pending?.request}
      submit={execution.submit}
      datasetId={datasetId}
      selectDataset={selectDataset}
      compact={view === 'overview'}
      selectedWorker={inspectedCount(inspectionCounts, selected)}
      {...(inspectionCounts.length > 0 && { inspectionCounts })}
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
        <div className="stack">{form}</div>
        {formMode !== 'benchmark' ? (
          <OperationStatus execution={operations} />
        ) : (
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
        )}
        <LiveWorkerMonitor
          key={operations.runId}
          acceptedRunId={operations.runId}
          locked={operations.running}
          onState={operations.observe}
        />
      </div>
    );

  if (view === 'api') return <ApiStatus health={health} />;

  if (workerResults)
    return (
      <AllWorkerResults
        id={comparisonId}
        history={history}
        selected={selected}
        select={select}
      />
    );

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
