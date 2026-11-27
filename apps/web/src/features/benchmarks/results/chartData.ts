import {
  maxCustomWorkerCount,
  workers,
  type WorkerSummary,
} from '@/api/contracts';

export const chartDefinitions = [
  {
    id: 'elapsed',
    title: 'Execution time comparison',
    unit: 'Seconds',
    note: 'Mean batch wall time · lower is better',
    type: 'bar',
  },
  {
    id: 'cpu',
    title: 'CPU utilization',
    unit: '% of one logical core',
    note: 'Observed process tree · can exceed 100%',
    type: 'line',
  },
  {
    id: 'throughput',
    title: 'Completed image throughput',
    unit: 'Completed images / second',
    note: 'Mean of measured per-run rates',
    type: 'bar',
  },
  {
    id: 'memory',
    title: 'Peak resident memory',
    unit: 'MiB',
    note: 'Maximum observed process-tree RSS · sampled',
    type: 'bar',
  },
  {
    id: 'speedup',
    title: 'Measured vs ideal speedup',
    unit: 'Speedup ×',
    note: 'Ratio of means against the 1-worker process pool',
    type: 'line',
  },
  {
    id: 'efficiency',
    title: 'Parallel efficiency',
    unit: 'Efficiency %',
    note: 'Server-computed speedup / workers × 100',
    type: 'line',
  },
] as const;
export type ChartMetric = (typeof chartDefinitions)[number]['id'];

/** Return only counts with recorded measured rounds, in numeric order. */
export function measuredCounts(summaries: readonly WorkerSummary[]): number[] {
  return [
    ...new Set(
      summaries
        .filter(
          (summary) =>
            summary.measured_count > 0 &&
            Number.isInteger(summary.workers) &&
            summary.workers > 0 &&
            summary.workers <= maxCustomWorkerCount,
        )
        .map((summary) => summary.workers),
    ),
  ].sort((left, right) => left - right);
}

function elapsedRanking(summaries: readonly WorkerSummary[]) {
  return summaries
    .flatMap((summary) => {
      const mean = summary.elapsed?.mean;
      return summary.complete &&
        typeof mean === 'number' &&
        Number.isFinite(mean) &&
        mean > 0
        ? [{ count: summary.workers, mean }]
        : [];
    })
    .sort((left, right) => left.mean - right.mean || left.count - right.count);
}

export function fastestCounts(summaries: readonly WorkerSummary[]): number[] {
  const counts = measuredCounts(summaries);
  const ranking = elapsedRanking(summaries).filter((item) =>
    counts.includes(item.count),
  );
  return [
    ...new Set(
      ranking
        .filter((item) => item.mean === ranking[0]?.mean)
        .map((item) => item.count),
    ),
  ];
}

/** Pin measured baseline and selection before filling the remaining elapsed slots. */
export function highlightCounts(
  summaries: readonly WorkerSummary[],
  selected: number,
): number[] {
  const counts = measuredCounts(summaries);
  if (counts.length <= 4) return counts;
  const priority = [
    1,
    selected,
    ...elapsedRanking(summaries).map((item) => item.count),
    ...counts,
  ];
  return [...new Set(priority.filter((count) => counts.includes(count)))]
    .slice(0, 4)
    .sort((left, right) => left - right);
}

export function inspectedCount(
  counts: readonly number[],
  preferred: number,
): number {
  return counts.includes(preferred) ? preferred : (counts[0] ?? preferred);
}
/**
 * Project server-owned summaries onto the visible axis without aggregation.
 * Incomplete comparisons and missing observations remain null, including sampled
 * CPU. CPU uses percent of one logical core; RSS uses MiB; elapsed time uses seconds.
 * @param summaries Summaries belonging to one persisted comparison context.
 * @param kind Metric to present, using the server's statistical definition.
 * @param counts Ordered worker axis. The fixed matrix remains the legacy default.
 * @param includePartial Display genuine partial metrics in the drill-down.
 * @returns Ordered values or nulls without rounding, interpolation, or zero filling.
 * @example
 * chartValues([], 'cpu'); // [null, null, null, null]
 */
export function chartValues(
  summaries: readonly WorkerSummary[],
  kind: ChartMetric,
  counts: readonly number[] = workers,
  includePartial = false,
): (number | null)[] {
  return counts.map((count) => {
    const summary = summaries.find(
      (item) => item.workers === count && (item.complete || includePartial),
    );
    if (!summary) return null;
    switch (kind) {
      case 'elapsed':
        return summary.elapsed?.mean ?? null;
      case 'cpu':
        return summary.cpu_mean_core_percent;
      case 'throughput':
        return summary.mean_throughput;
      case 'memory':
        return summary.rss_peak_mib;
      case 'speedup':
        return summary.speedup;
      case 'efficiency':
        return summary.efficiency_percent;
    }
  });
}
