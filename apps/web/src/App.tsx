import { useEffect, useState } from 'react';

import { isTerminal, type WorkerCount } from '@/api/contracts';
import Shell from '@/components/Shell';
import { useNavigation } from '@/components/useNavigation';
import BenchmarkNotices from '@/features/benchmarks/BenchmarkNotices';
import BenchmarkWorkspace from '@/features/benchmarks/BenchmarkWorkspace';
import { useCatalog, useHistory } from '@/features/benchmarks/useHistory';
import { useJob } from '@/features/benchmarks/useJob';

export default function App() {
  const { view, navigate } = useNavigation();
  const catalog = useCatalog();
  const execution = useJob();
  const terminalId =
    execution.job && isTerminal(execution.job.state) ? execution.job.id : null;
  const history = useHistory(terminalId);
  const [selected, setSelected] = useState<WorkerCount>(4);

  useEffect(() => {
    if (view === 'history') document.getElementById('history-search')?.focus();
  }, [view]);

  const active =
    execution.recovering ||
    Boolean(execution.job && !isTerminal(execution.job.state));

  return (
    <Shell view={view} navigate={navigate} active={active}>
      <div className="stack">
        <BenchmarkNotices
          catalog={catalog}
          historyError={history.error}
          retryHistory={history.reload}
          execution={execution}
          active={active}
          view={view}
        />
        <BenchmarkWorkspace
          view={view}
          navigate={navigate}
          datasets={catalog.datasets}
          execution={execution}
          active={active}
          history={history}
          selected={selected}
          select={setSelected}
        />
        <p className="methodology">
          ◉ &nbsp; Single-host process-pool measurements. Independent
          experiments are never pooled into one scaling curve. Unavailable
          metrics are not zero.
        </p>
      </div>
    </Shell>
  );
}
