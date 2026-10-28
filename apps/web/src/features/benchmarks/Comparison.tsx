import type { ReactNode } from 'react';

import type { WorkerCount } from '@/api/contracts';
import Select from '@/components/Select';

import Results from './Results';
import HistoryInspector from './HistoryInspector';
import type { useHistory } from './useHistory';

interface Props {
  readonly history: ReturnType<typeof useHistory>;
  readonly selected: WorkerCount;
  readonly select: (workers: WorkerCount) => void;
  readonly children: ReactNode;
  readonly historyView?: boolean;
}

export default function Comparison({
  history,
  selected,
  select,
  children,
  historyView = false,
}: Props) {
  return (
    <>
      <div className="history-toolbar">
        <div className="form-field">
          <label htmlFor="saved-comparison">Saved comparison</label>
          <Select
            id="saved-comparison"
            label="Saved comparison"
            value={history.selectedId}
            placeholder="Choose an experiment"
            disabled={history.items.length === 0}
            onChange={history.select}
            options={history.items.map(({ job }) => ({
              value: job.id,
              label:
                job.request.dataset_id +
                ' · ' +
                job.id.slice(0, 8) +
                ' · ' +
                job.state,
            }))}
          />
        </div>
        <button
          type="button"
          onClick={history.reload}
          disabled={history.loading}
        >
          Refresh history
        </button>
      </div>
      {history.error && !historyView && (
        <p className="request-error">
          {history.error}{' '}
          <button type="button" onClick={history.reload}>
            Retry history
          </button>
        </p>
      )}
      {history.detailError && history.detailError !== history.error && (
        <p className="request-error">
          {history.detailError}{' '}
          <button type="button" onClick={history.reload}>
            Retry evidence
          </button>
        </p>
      )}
      {history.detailLoading && !history.detail && !history.detailError && (
        <output>Loading experiment evidence…</output>
      )}
      {historyView ? (
        <div className="history-screen">
          {children}
          <HistoryInspector detail={history.detail} selected={selected} />
        </div>
      ) : null}
      <Results detail={history.detail} selected={selected} select={select} />
      {!historyView && history.detail && (
        <a
          href={`/api/benchmarks/${encodeURIComponent(history.detail.job.id)}`}
          target="_blank"
          rel="noreferrer"
        >
          Open raw API evidence JSON ↗
        </a>
      )}
    </>
  );
}
