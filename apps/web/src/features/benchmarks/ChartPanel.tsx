import type { ReactNode } from 'react';

import type { WorkerCount, WorkerSummary } from '@/api/contracts';

import { chartDefinitions, chartValues, type ChartMetric } from './chartData';
import { metric } from './format';

export default function ChartPanel({
  kind,
  selected,
  summaries,
  overview = false,
  children,
}: {
  readonly kind: ChartMetric;
  readonly selected: WorkerCount;
  readonly summaries: readonly WorkerSummary[];
  readonly overview?: boolean;
  readonly children: ReactNode;
}) {
  const definition = chartDefinitions.find((item) => item.id === kind);
  if (!definition) return null;
  const summary = summaries.find(
    (item) => item.workers === selected && item.complete,
  );
  const baseline = summaries.find(
    (item) => item.workers === 1 && item.complete,
  );
  let note = 'Select a point to inspect a worker count.';
  if (definition.type === 'bar')
    note = 'Select a bar to inspect a worker count.';
  if (kind === 'elapsed')
    note += ` Baseline: ${metric(baseline?.elapsed?.mean, ' seconds', 1)}.`;
  if (chartValues(summaries, kind).every((value) => value === null))
    note = 'No observations available for this metric.';
  return (
    <section
      className={`card chart-card ${kind}${overview ? ' overview-chart' : ''}`}
      aria-labelledby={`chart-${kind}`}
    >
      <div className="panel-heading">
        <div>
          <h2 id={`chart-${kind}`}>{definition.title}</h2>
          <p>{definition.note}</p>
        </div>
        {kind === 'cpu' ? (
          <span className="chart-key">
            <i /> CPU
          </span>
        ) : (
          <span className="badge">
            {overview ? 'Resize + Grayscale' : 'Measured'}
          </span>
        )}
      </div>
      {overview && kind === 'elapsed' && (
        <div className="chart-legend">
          <span className="chart-key">
            <i className="muted" /> Other workers
          </span>
          <span className="chart-key">
            <i />
            Selected · {selected}W
          </span>
        </div>
      )}
      {children}
      {overview && kind === 'cpu' ? (
        <div className="cpu-footer">
          <span>Selected: {selected} workers</span>
          <strong>
            {metric(summary?.cpu_mean_core_percent, '% CPU')} ·{' '}
            {metric(summary?.rss_peak_mib, ' MiB')}
          </strong>
        </div>
      ) : (
        <p className="chart-note">{note}</p>
      )}
    </section>
  );
}
