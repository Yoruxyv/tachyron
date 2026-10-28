import { useState } from 'react';

import { type HistoryItem, type WorkerCount } from '@/api/contracts';
import Select from '@/components/Select';

import { timestamp } from '@/lib/format';
import RecentExperiments from './RecentExperiments';

interface Props {
  readonly items: readonly HistoryItem[];
  readonly selectedId: string;
  readonly select: (id: string) => void;
  readonly loading: boolean;
  readonly initialLoading: boolean;
  readonly offset: number;
  readonly setOffset: (offset: number) => void;
  readonly compact?: boolean;
  readonly worker?: WorkerCount;
  readonly error: string;
  readonly retry: () => void;
}
export default function History({
  items,
  selectedId,
  select,
  loading,
  initialLoading,
  offset,
  setOffset,
  compact = false,
  worker = 4,
  error,
  retry,
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
    <section
      className={`card history-card${compact ? ' compact' : ''}`}
      aria-labelledby="history-heading"
    >
      <div className="panel-heading">
        <div>
          <h2 id="history-heading">
            {compact ? 'Recent experiments' : 'Recorded experiments'}
          </h2>
          <p>
            {compact
              ? `Saved comparisons · showing ${worker}-worker results`
              : 'Persisted on the local API · newest first'}
          </p>
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
          <div className="form-field">
            <label htmlFor="history-state">Status</label>
            <Select
              id="history-state"
              label="Status"
              value={state}
              onChange={setState}
              options={[
                { value: 'all', label: 'All terminal states' },
                { value: 'COMPLETED', label: 'Completed' },
                { value: 'FAILED', label: 'Failed' },
                { value: 'CANCELLED', label: 'Cancelled' },
              ]}
            />
          </div>
        </div>
      )}
      {error && (
        <p className="request-error">
          {error}{' '}
          <button type="button" onClick={retry} disabled={loading}>
            Retry history
          </button>
        </p>
      )}
      {initialLoading ? (
        <output className="empty">Loading saved experiments…</output>
      ) : (
        <>
          {compact && shown.length > 0 && (
            <RecentExperiments
              items={shown}
              worker={worker}
              selectedId={selectedId}
              select={select}
            />
          )}
          {!compact && (
            <div className="history-records">
              <div className="history-record-head" aria-hidden="true">
                <span>Run</span>
                <span>Operation / accepted</span>
                <span>Workers</span>
                <span>Status</span>
              </div>
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
          )}
          {shown.length === 0 && !error && (
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
