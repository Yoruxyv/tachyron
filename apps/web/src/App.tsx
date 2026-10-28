import { isTerminal, type WorkerCount } from '@/api/contracts';
import Shell from '@/components/Shell';
import { useNavigation } from '@/components/useNavigation';
import ApiStatus from '@/features/benchmarks/ApiStatus';
import History from '@/features/benchmarks/History';
import JobForm from '@/features/benchmarks/JobForm';
import JobProgress from '@/features/benchmarks/JobProgress';
import Results, { Kpis } from '@/features/benchmarks/Results';
import { useCatalog, useHistory } from '@/features/benchmarks/useHistory';
import { useJob } from '@/features/benchmarks/useJob';
import { useEffect, useState } from 'react';

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
  const form = (
    <JobForm
      datasets={catalog.datasets}
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
  return (
    <Shell view={view} navigate={navigate} active={active}>
      <div className="stack">
        {catalog.error && (
          <div className="notice" role="alert">
            {catalog.error}{' '}
            <button type="button" onClick={catalog.reload}>
              Retry datasets
            </button>
          </div>
        )}
        {history.error && (
          <div className="notice" role="alert">
            {history.error}{' '}
            <button type="button" onClick={history.reload}>
              Retry history
            </button>
          </div>
        )}
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
        {catalog.loading && <output>Loading registered datasets…</output>}
        {!catalog.loading &&
          !catalog.error &&
          catalog.datasets.length === 0 && (
            <p className="notice">
              No datasets registered. Configure TACHYRON_DATASETS on the local
              server, then restart it and retry datasets.{' '}
              <button type="button" onClick={catalog.reload}>
                Retry datasets
              </button>
            </p>
          )}
        {active && view !== 'experiments' && (
          <output className="notice">
            {execution.job?.state ?? 'Recovering experiment'} ·{' '}
            <a href="#experiments">View job progress</a>
          </output>
        )}
        {view === 'overview' && (
          <>
            <Kpis detail={history.detail} selected={selected} />
            <Results
              detail={history.detail}
              selected={selected}
              select={setSelected}
              overview
            />
            <div className="overview-bottom">
              {records}
              {form}
            </div>
          </>
        )}
        {view === 'experiments' && (
          <div className="experiment-grid">
            {form}
            <JobProgress
              job={execution.job}
              busy={execution.busy}
              cancel={execution.cancel}
            />
          </div>
        )}
        {(view === 'compare' || view === 'history') && (
          <>
            <div className="history-toolbar">
              <label>
                <span>Saved comparison</span>
                <select
                  value={history.selectedId}
                  onChange={(event) => history.select(event.target.value)}
                >
                  <option value="">Choose an experiment</option>
                  {history.items.map(({ job }) => (
                    <option key={job.id} value={job.id}>
                      {job.request.dataset_id} · {job.id.slice(0, 8)} ·{' '}
                      {job.state}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                onClick={history.reload}
                disabled={history.loading}
              >
                Refresh history
              </button>
            </div>
            {view === 'history' && records}
            {history.detailLoading && (
              <output>Loading experiment evidence…</output>
            )}
            {history.detailError && (
              <p role="alert" className="notice">
                {history.detailError}{' '}
                <button type="button" onClick={history.reload}>
                  Retry evidence
                </button>
              </p>
            )}
            <Kpis detail={history.detail} selected={selected} />
            <Results
              detail={history.detail}
              selected={selected}
              select={setSelected}
            />
            {history.detail && (
              <a
                href={`/api/benchmarks/${encodeURIComponent(history.detail.job.id)}`}
                target="_blank"
                rel="noreferrer"
              >
                Open raw API evidence JSON ↗
              </a>
            )}
          </>
        )}
        {view === 'api' && <ApiStatus />}
        <p className="methodology">
          ◉ &nbsp; Single-host process-pool measurements. Independent
          experiments are never pooled into one scaling curve. Unavailable
          metrics are not zero.
        </p>
      </div>
    </Shell>
  );
}
