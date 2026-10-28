import type { HistoryItem, WorkerCount } from '@/api/contracts';

import { metric } from './format';

export default function RecentExperiments({
  items,
  worker,
  selectedId,
  select,
}: {
  readonly items: readonly HistoryItem[];
  readonly worker: WorkerCount;
  readonly selectedId: string;
  readonly select: (id: string) => void;
}) {
  return (
    <div className="recent-scroller">
      <table className="recent-table">
        <caption className="sr-only">
          Saved experiments. Time and speedup show {worker}-worker measurements
          from each independent 1/2/4/8-worker comparison.
        </caption>
        <thead>
          <tr>
            {['Run', 'Operation', 'Workers', 'Time', 'Speedup', 'Status'].map(
              (title) => (
                <th scope="col" key={title}>
                  {title}
                </th>
              ),
            )}
          </tr>
        </thead>
        <tbody>
          {items.map(({ job, summaries }) => {
            const summary =
              job.state === 'COMPLETED'
                ? summaries?.find(
                    (item) => item.workers === worker && item.complete,
                  )
                : undefined;
            return (
              <tr key={job.id} data-selected={selectedId === job.id}>
                <td className="recent-name">
                  <button
                    type="button"
                    onClick={() => select(job.id)}
                    aria-pressed={selectedId === job.id}
                    title={job.id}
                  >
                    {job.request.dataset_id}
                    <small>{job.id.slice(0, 8)}</small>
                  </button>
                </td>
                <td>Resize + Grayscale</td>
                <td className="mono">{worker}</td>
                <td className="mono">
                  {metric(summary?.elapsed?.mean, 's', 1)}
                </td>
                <td className="mono">{metric(summary?.speedup, '×', 2, 2)}</td>
                <td>
                  <span className={`recent-status ${job.state.toLowerCase()}`}>
                    {job.state.toLowerCase()}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
