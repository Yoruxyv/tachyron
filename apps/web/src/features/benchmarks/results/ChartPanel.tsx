import type { ReactNode } from 'react';

import type { WorkerSummary } from '@/api/contracts';

import {
  chartDefinitions,
  chartValues,
  fastestCounts,
  highlightCounts,
  measuredCounts,
  type ChartMetric,
} from './chartData';
import { metric } from '@/lib/format';

export default function ChartPanel({
  kind,
  selected,
  summaries,
  overview = false,
  full = false,
  resultsHref,
  children,
}: {
  readonly kind: ChartMetric;
  readonly selected: number;
  readonly summaries: readonly WorkerSummary[];
  readonly overview?: boolean;
  readonly full?: boolean;
  readonly resultsHref?: string | undefined;
  readonly children: ReactNode;
}) {
  const definition = chartDefinitions.find((item) => item.id === kind);
  if (!definition) return null;
  const summary = summaries.find(
    (item) => item.workers === selected && item.complete,
  );
  const baseline = summaries.find(
    (item) => item.workers === 1 && (item.complete || full),
  );
  let note = 'Select a point to inspect a worker count.';
  if (definition.type === 'bar')
    note = 'Select a bar to inspect a worker count.';
  if (
    chartValues(summaries, kind, measuredCounts(summaries), full).every(
      (value) => value === null,
    )
  )
    note = 'No observations available for this metric.';
  if (kind === 'elapsed')
    note += ` One-worker pool baseline · ${metric(baseline?.elapsed?.mean, ' seconds', 1)}.`;
  const total = measuredCounts(summaries).length;
  const highlights =
    kind === 'elapsed' && !full
      ? highlightCounts(summaries, selected).length
      : total;
  const fastest = kind === 'elapsed' ? fastestCounts(summaries) : [];
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
        <div className="chart-caption">
          <p className="chart-note">{note}</p>
          {highlights < total && (
            <p className="chart-note">
              Showing {highlights} of {total} measured configurations.
            </p>
          )}
          {fastest.length > 0 && (
            <p className="chart-note">
              {fastest.length > 1
                ? 'Tied lowest observed mean'
                : 'Lowest observed mean'}{' '}
              · {fastest.map((count) => `${count}W`).join(', ')}.
            </p>
          )}
          {kind === 'elapsed' && resultsHref && !full && (
            <a href={resultsHref} className="worker-results-link">
              View all worker results <span aria-hidden="true">↗</span>
            </a>
          )}
        </div>
      )}
    </section>
  );
}
