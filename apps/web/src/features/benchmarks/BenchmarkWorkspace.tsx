import type { Dataset, WorkerCount } from '@/api/contracts';
import type { View } from '@/components/useNavigation';

import ApiStatus from './ApiStatus';
import Comparison from './Comparison';
import History from './History';
import JobForm from './JobForm';
import JobProgress from './JobProgress';
import Results, { Kpis } from './Results';
import type { useHistory } from './useHistory';
import type { useJob } from './useJob';

interface Props {
  readonly view: View;
  readonly navigate: (view: View) => void;
  readonly datasets: Dataset[];
  readonly execution: ReturnType<typeof useJob>;
  readonly active: boolean;
  readonly history: ReturnType<typeof useHistory>;
  readonly selected: WorkerCount;
  readonly select: (workers: WorkerCount) => void;
}

/**
 * Compose view content without owning requests or persistent selection.
 * App keeps lifecycle hooks and worker inspection mounted across navigation.
 */
export default function BenchmarkWorkspace({
  view,
  navigate,
  datasets,
  execution,
  active,
  history,
  selected,
  select,
}: Props) {
  const form = (
    <JobForm
      datasets={datasets}
      disabled={execution.busy || active || execution.coolingDown}
      pending={execution.pending?.request}
      submit={execution.submit}
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
      offset={history.offset}
      setOffset={history.setOffset}
      compact={view === 'overview'}
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
        {form}
        <JobProgress
          job={execution.job}
          busy={execution.busy}
          cancel={execution.cancel}
        />
      </div>
    );

  if (view === 'api') return <ApiStatus />;

  return (
    <Comparison history={history} selected={selected} select={select}>
      {view === 'history' && records}
    </Comparison>
  );
}
