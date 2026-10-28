import { describe, expect, it } from 'vitest';

import {
  chartDefinitions,
  chartValues,
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
