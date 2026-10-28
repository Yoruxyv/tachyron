import Shell from '@/components/Shell';
import BenchmarkNotices from '@/features/benchmarks/BenchmarkNotices';
import BenchmarkWorkspace from '@/features/benchmarks/BenchmarkWorkspace';
import WorkspaceStatus from '@/features/benchmarks/WorkspaceStatus';
import ApiNotifications from '@/features/benchmarks/ApiNotifications';
import { useWorkspace } from '@/features/benchmarks/useWorkspace';

export default function App() {
  const {
    view,
    navigate,
    catalog,
    execution,
    history,
    health,
    selected,
    selectWorker,
    datasetId,
    selectDataset,
    active,
    configurationLocked,
    jobVerified,
    confirmedActive,
    backToTop,
  } = useWorkspace();

  return (
    <Shell
      view={view}
      navigate={navigate}
      active={confirmedActive}
      datasets={catalog.datasets}
      datasetId={datasetId}
      selectDataset={selectDataset}
      configurationLocked={configurationLocked}
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
          select={selectWorker}
          datasetId={datasetId}
          selectDataset={selectDataset}
          health={health}
          jobVerified={jobVerified}
        />
        <p className="methodology">
          <span>
            ◉ &nbsp; Single-host process-pool measurements. Independent
            experiments are never pooled into one scaling curve. Unavailable
            metrics are not zero.
          </span>
          <button type="button" className="back-to-top" onClick={backToTop}>
            Back to top ↑
          </button>
        </p>
      </div>
    </Shell>
  );
}
