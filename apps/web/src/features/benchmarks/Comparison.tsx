import type { ReactNode } from 'react';

import type { WorkerCount } from '@/api/contracts';

import Results, { Kpis } from './Results';
import type { useHistory } from './useHistory';

interface Props {
  readonly history: ReturnType<typeof useHistory>;
  readonly selected: WorkerCount;
  readonly select: (workers: WorkerCount) => void;
  readonly children: ReactNode;
}

export default function Comparison({
  history,
  selected,
  select,
  children,
}: Props) {
  return (
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
                {job.request.dataset_id} · {job.id.slice(0, 8)} · {job.state}
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
      {children}
      {history.detailLoading && <output>Loading experiment evidence…</output>}
      {history.detailError && (
        <p role="alert" className="notice">
          {history.detailError}{' '}
          <button type="button" onClick={history.reload}>
            Retry evidence
          </button>
        </p>
      )}
      <Kpis detail={history.detail} selected={selected} />
      <Results detail={history.detail} selected={selected} select={select} />
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
  );
}
