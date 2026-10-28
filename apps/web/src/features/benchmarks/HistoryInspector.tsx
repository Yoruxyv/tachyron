import type { HistoryDetail, WorkerCount } from '@/api/contracts';

import { metric, timestamp } from './format';

export default function HistoryInspector({
  detail,
  selected,
}: {
  readonly detail: HistoryDetail | null;
  readonly selected: WorkerCount;
}) {
  const summary =
    detail?.job.state === 'COMPLETED' &&
    detail.result?.experiment.status === 'success'
      ? detail.result.summaries.find(
          (item) => item.workers === selected && item.complete,
        )
      : undefined;
  return (
    <section className="card history-inspector">
      <div className="panel-heading">
        <div>
          <h2>Selected run</h2>
          <p>Persisted evidence for one comparison</p>
        </div>
        {detail && (
          <span className={`state ${detail.job.state.toLowerCase()}`}>
            {detail.job.state}
          </span>
        )}
      </div>
      {detail ? (
        <>
          <p className="fine-print">
            Inspecting {selected} workers · full scaling evidence below
          </p>
          <dl className="inspector-metrics">
            {[
              ['Dataset', detail.job.request.dataset_id],
              ['Accepted', timestamp(detail.job.created_at)],
              ['Operation', 'Resize + Grayscale'],
              [
                'Output',
                `${detail.job.request.width} × ${detail.job.request.height} px`,
              ],
              ['Time mean', metric(summary?.elapsed?.mean, ' s')],
              ['Speedup', metric(summary?.speedup, '×')],
              [
                'CPU (% of one core)',
                metric(summary?.cpu_mean_core_percent, '%'),
              ],
              ['Peak RSS', metric(summary?.rss_peak_mib, ' MiB')],
              ['Measured runs', metric(summary?.measured_count)],
            ].map(([label, value]) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
          {detail.job.request.tiny && (
            <p className="fine-print">
              Tiny functional test; not performance evidence.
            </p>
          )}
          <a
            href={`/api/benchmarks/${encodeURIComponent(detail.job.id)}`}
            target="_blank"
            rel="noreferrer"
          >
            Open raw API evidence JSON ↗
          </a>
        </>
      ) : (
        <p className="empty">
          Select a saved experiment to inspect its evidence.
        </p>
      )}
    </section>
  );
}
