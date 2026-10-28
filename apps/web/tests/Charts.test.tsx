import { render, screen } from '@testing-library/react';
import type * as ChartJs from 'chart.js';
import { describe, expect, it, vi } from 'vitest';

import Charts from '@/features/benchmarks/Charts';

import { summaries } from './fixtures';

const lifecycle = vi.hoisted(() => {
  const configurations: ChartJs.ChartConfiguration<
    'bar' | 'line',
    (number | null)[]
  >[] = [];
  return { destroy: vi.fn(), configurations };
});

vi.mock('chart.js', async () => {
  const actual = await vi.importActual<typeof ChartJs>('chart.js');
  return {
    ...actual,
    Chart: class {
      static readonly register = vi.fn();
      destroy = lifecycle.destroy;

      constructor(
        _canvas: HTMLCanvasElement,
        config: ChartJs.ChartConfiguration<'bar' | 'line', (number | null)[]>,
      ) {
        lifecycle.configurations.push(config);
      }
    },
  };
});

describe('Chart.js lifecycle and presentation', () => {
  it('owns six instances, adds only the theoretical speedup series, and destroys replacements', () => {
    lifecycle.configurations.length = 0;
    const select = vi.fn();
    const { rerender, unmount } = render(
      <Charts summaries={summaries} selected={4} select={select} />,
    );
    expect(lifecycle.configurations).toHaveLength(6);
    expect(
      screen.getByRole('heading', { name: 'Measured vs ideal speedup' }),
    ).toBeVisible();
    const speedup = lifecycle.configurations[4];
    expect(speedup?.data.datasets.map((series) => series.label)).toEqual([
      'Speedup ×',
      'Theoretical ideal',
    ]);
    expect(speedup?.data.datasets[1]?.data).toEqual([1, 2, 4, 8]);
    expect(lifecycle.configurations[1]?.data.datasets[0]?.data).toEqual([
      70, 140, 280, 560,
    ]);
    rerender(<Charts summaries={summaries} selected={8} select={select} />);
    expect(lifecycle.destroy).toHaveBeenCalledTimes(6);
    unmount();
    expect(lifecycle.destroy).toHaveBeenCalledTimes(12);
  });
});
