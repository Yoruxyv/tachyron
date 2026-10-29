import Shell from '@/components/Shell';
import BenchmarkNotices from '@/app/BenchmarkNotices';
import BenchmarkWorkspace from '@/app/BenchmarkWorkspace';
import WorkspaceStatus from '@/features/diagnostics/WorkspaceStatus';
import ApiNotifications from '@/features/diagnostics/ApiNotifications';
import { useWorkspace } from '@/app/useWorkspace';
import DatasetImport from '@/features/datasets/components/DatasetImport';
import DatasetFolderPicker from '@/features/datasets/components/DatasetFolderPicker';

export default function App() {
  const {
    view,
    navigate,
    catalog,
    importer,
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
          importFeedback={importer.feedback}
          importDialogOpen={importer.open}
        />
        <BenchmarkNotices
          catalog={catalog}
          execution={execution}
          active={active}
          view={view}
          jobVerified={jobVerified}
        />
        <BenchmarkWorkspace
          importing={importer.busy}
          importAction={
            <DatasetFolderPicker
              disabled={configurationLocked}
              choose={importer.choose}
            />
          }
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
        {importer.open && (
          <DatasetImport
            importer={importer}
            disabled={configurationLocked && !importer.busy}
          />
        )}
        {importer.busy && !importer.open && (
          <output>
            Dataset import active.{' '}
            <button type="button" onClick={importer.show}>
              Review import
            </button>
          </output>
        )}
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
