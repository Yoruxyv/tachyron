import { useState } from 'react';

import { type HistoryItem } from '@/api/contracts';

import { timestamp } from './format';

interface Props {
  readonly items: readonly HistoryItem[];
  readonly selectedId: string;
  readonly select: (id: string) => void;
  readonly loading: boolean;
  readonly offset: number;
  readonly setOffset: (offset: number) => void;
  readonly compact?: boolean;
}
export default function History({
  items,
  selectedId,
  select,
  loading,
  offset,
  setOffset,
  compact = false,
}: Props) {
  const [search, setSearch] = useState('');
  const [state, setState] = useState('all');
  const filtered = items.filter(
    ({ job }) =>
      `${job.id} ${job.request.dataset_id}`
        .toLowerCase()
        .includes(search.toLowerCase()) &&
      (state === 'all' || job.state === state),
  );
  const shown = compact ? filtered.slice(0, 5) : filtered;
  return (
    <section className="card history-card" aria-labelledby="history-heading">
      <div className="panel-heading">
        <div>
          <h2 id="history-heading">
            {compact ? 'Recent experiments' : 'Recorded experiments'}
          </h2>
          <p>Persisted on the local API · newest first</p>
        </div>
        {compact && <a href="#history">View all ↗</a>}
      </div>
      {!compact && (
        <div className="history-filters">
          <label>
            <span>Search runs</span>
            <input
              id="history-search"
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Dataset or job ID"
            />
          </label>
          <label>
            <span>Status</span>
            <select
              value={state}
              onChange={(event) => setState(event.target.value)}
            >
              <option value="all">All terminal states</option>
              <option value="COMPLETED">Completed</option>
              <option value="FAILED">Failed</option>
              <option value="CANCELLED">Cancelled</option>
            </select>
          </label>
        </div>
      )}
      {loading ? (
        <output className="empty">Loading saved experiments…</output>
      ) : (
        <>
          <div className="history-records">
            {shown.map(({ job }) => (
              <button
                className="history-record"
                type="button"
                key={job.id}
                aria-pressed={selectedId === job.id}
                onClick={() => select(job.id)}
              >
                <span>
                  <strong>{job.request.dataset_id}</strong>
                  <small className="mono">{job.id.slice(0, 8)}</small>
                </span>
                <span>
                  <strong>Resize + grayscale</strong>
                  <small>
                    {job.input_count} images · {timestamp(job.created_at)}
                  </small>
                </span>
                <span className="mono">1/2/4/8W</span>
                <span className={`state ${job.state.toLowerCase()}`}>
                  {job.state}
                </span>
              </button>
            ))}
          </div>
          {shown.length === 0 && (
            <p className="empty">
              {items.length
                ? 'No matching records on this page.'
                : 'No benchmark runs yet. Start an experiment to save measured results here.'}
            </p>
          )}
        </>
      )}
      {!compact && (
        <div className="pagination">
          <button
            type="button"
            disabled={loading || offset === 0}
            onClick={() => setOffset(Math.max(0, offset - 20))}
          >
            Previous page
          </button>
          <span>Page {offset / 20 + 1}</span>
          <button
            type="button"
            disabled={loading || items.length < 20 || offset >= 100000}
            onClick={() => setOffset(offset + 20)}
          >
            Next page
          </button>
          <p className="fine-print">
            Search and status filters apply to this page.
          </p>
        </div>
      )}
    </section>
  );
}
