import { workers, type WorkerSummary } from '@/api/contracts';

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
/**
 * Project server-owned summaries onto the fixed worker axis without aggregation.
 * Incomplete comparisons and missing observations remain null, including sampled
 * CPU. CPU uses percent of one logical core; RSS uses MiB; elapsed time uses seconds.
 * @param summaries Summaries belonging to one persisted comparison context.
 * @param kind Metric to present, using the server's statistical definition.
 * @returns Four ordered values or nulls; no rounding, interpolation, or zero filling.
 * @example
 * chartValues([], 'cpu'); // [null, null, null, null]
 */
export function chartValues(
  summaries: readonly WorkerSummary[],
  kind: ChartMetric,
): (number | null)[] {
  return workers.map((count) => {
    const summary = summaries.find(
      (item) => item.workers === count && item.complete,
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
