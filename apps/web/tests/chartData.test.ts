import { describe, expect, it } from 'vitest';

import {
  chartDefinitions,
  chartValues,
  fastestCounts,
  highlightCounts,
  measuredCounts,
} from '@/features/benchmarks/results/chartData';

import { summaries } from './fixtures';

describe('chart transformations', () => {
  it('orders server values by the fixed worker matrix without recomputing statistics', () => {
    expect(chartValues([...summaries].reverse(), 'elapsed')).toEqual([
      8, 4, 2, 1,
    ]);
    expect(chartValues(summaries, 'cpu')).toEqual([70, 140, 280, 560]);
    expect(chartValues(summaries, 'throughput')).toEqual([1, 2, 4, 8]);
    expect(chartValues(summaries, 'memory')).toEqual([200, 400, 800, 1600]);
    expect(chartValues(summaries, 'speedup')).toEqual([1, 2, 4, 8]);
    expect(chartValues(summaries, 'efficiency')).toEqual([100, 100, 100, 100]);
  });

  it('preserves empty, partial, and unavailable observations as null', () => {
    expect(chartValues([], 'elapsed')).toEqual([null, null, null, null]);
    expect(
      chartValues(
        summaries.map((summary) => ({ ...summary, complete: false })),
        'elapsed',
      ),
    ).toEqual([null, null, null, null]);
    expect(
      chartValues(
        summaries.map((summary) => ({
          ...summary,
          cpu_mean_core_percent: null,
        })),
        'cpu',
      ),
    ).toEqual([null, null, null, null]);
  });

  it('pins the observed baseline and selection, then ranks unrounded elapsed time', () => {
    const rows = [9, 7, 5, 3, 2, 1].flatMap((workers, index) => {
      const template = summaries[0];
      if (!template?.elapsed) return [];
      const mean = [10, 1.001, 1.002, 1.001, 3, 20][index] ?? 0;
      return [{ ...template, workers, elapsed: { ...template.elapsed, mean } }];
    });
    expect(highlightCounts(rows, 9)).toEqual([1, 3, 7, 9]);
    expect(fastestCounts(rows)).toEqual([3, 7]);
    const withoutBaseline = rows.filter((row) => row.workers !== 1);
    expect(highlightCounts(withoutBaseline, 16)).toEqual([2, 3, 5, 7]);
  });

  it('shows partial measured data only in the full comparison and never adds unmeasured counts', () => {
    const rows = summaries.map((row) => ({
      ...row,
      complete: row.workers !== 4,
      measured_count: row.workers === 8 ? 0 : row.measured_count,
    }));
    const axis = measuredCounts(rows);
    expect(axis).toEqual([1, 2, 4]);
    expect(highlightCounts(rows, 8)).toEqual(axis);
    expect(chartValues(rows, 'elapsed', axis)).toEqual([8, 4, null]);
    expect(chartValues(rows, 'elapsed', axis, true)).toEqual([8, 4, 2]);
    expect(highlightCounts(summaries, 8)).toEqual([1, 2, 4, 8]);
  });

  it('labels all six metrics with the backend units and denominator', () => {
    expect(chartDefinitions).toHaveLength(6);
    expect(
      chartDefinitions.find((definition) => definition.id === 'cpu')?.unit,
    ).toBe('% of one logical core');
    expect(
      chartDefinitions.find((definition) => definition.id === 'memory')?.unit,
    ).toBe('MiB');
    expect(
      chartDefinitions.find((definition) => definition.id === 'throughput')
        ?.unit,
    ).toBe('Completed images / second');
  });
});
