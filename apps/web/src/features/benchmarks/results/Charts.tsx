import { useEffect, useRef } from 'react';
import {
  BarController,
  BarElement,
  CategoryScale,
  Chart,
  Legend,
  LinearScale,
  LineController,
  LineElement,
  PointElement,
  Tooltip,
  type ChartDataset,
  type Plugin,
  type ScriptableContext,
} from 'chart.js';

import type { WorkerSummary } from '@/api/contracts';

import {
  chartDefinitions,
  chartValues,
  highlightCounts,
  measuredCounts,
  type ChartMetric,
} from './chartData';
import ChartPanel from './ChartPanel';
import { metric } from '@/lib/format';

Chart.register(
  BarController,
  BarElement,
  CategoryScale,
  LinearScale,
  LineController,
  LineElement,
  PointElement,
  Tooltip,
  Legend,
);
interface Props {
  readonly summaries: readonly WorkerSummary[];
  readonly selected: number;
  readonly select: (count: number) => void;
  readonly overview?: boolean;
  readonly resultsHref?: string | undefined;
}
/** Own one Chart.js instance; destroy it before replacing its data or selection. */
export function MetricChart({
  summaries,
  selected,
  select,
  kind,
  overview = false,
  full = false,
  resultsHref,
}: Props & { readonly kind: ChartMetric; readonly full?: boolean }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const definition = chartDefinitions.find((item) => item.id === kind);
  useEffect(() => {
    if (!canvas.current || !definition) return;
    const axis =
      kind === 'elapsed' && !full
        ? highlightCounts(summaries, selected)
        : measuredCounts(summaries);
    const values = chartValues(summaries, kind, axis, full);
    const datasets: ChartDataset<'bar' | 'line', (number | null)[]>[] = [
      {
        label: definition.unit,
        data: values,
        backgroundColor: (
          context: ScriptableContext<'bar'> | ScriptableContext<'line'>,
        ) => {
          const chosen = axis[context.dataIndex] === selected;
          const area = context.chart.chartArea;
          const barTop = context.chart.scales.y?.getPixelForValue(
            context.parsed?.y ?? 0,
          );
          const top = Number.isFinite(barTop)
            ? (barTop ?? 0)
            : (area?.top ?? 0);
          const gradient = context.chart.ctx.createLinearGradient(
            0,
            top,
            0,
            area?.bottom ?? context.chart.height,
          );
          gradient.addColorStop(0, chosen ? '#f0f2f5' : '#606b77');
          gradient.addColorStop(1, chosen ? '#b5bdc8' : '#363e48');
          return gradient;
        },
        borderColor: '#d7dbe1',
        borderWidth: definition.type === 'line' ? 2.5 : 0,
        borderRadius: 3,
        maxBarThickness: 86,
        categoryPercentage: 0.75,
        barPercentage: 1,
        pointBorderColor: '#171a1f',
        pointBackgroundColor: axis.map((count) =>
          count === selected ? '#f3f4f6' : '#858f9b',
        ),
        pointBorderWidth: 2,
        pointRadius: axis.map((count) => (count === selected ? 6 : 4.5)),
      },
    ];
    if (kind === 'speedup')
      datasets.push({
        label: 'Theoretical ideal',
        type: 'line',
        data: [...axis],
        borderColor: '#8e98a6',
        borderDash: [5, 5],
        pointStyle: 'triangle',
        pointRadius: 4,
        backgroundColor: '#8e98a6',
      });
    const valueLabels: Plugin<'bar' | 'line'> = {
      id: 'overview-values',
      afterDatasetsDraw: (chart) => {
        if (!overview || kind !== 'elapsed') return;
        const context = chart.ctx;
        context.save();
        context.font = '9px "IBM Plex Mono", monospace';
        context.textAlign = 'center';
        context.textBaseline = 'top';
        chart.getDatasetMeta(0).data.forEach((bar, index) => {
          const value = values[index];
          if (value === null || value === undefined) return;
          context.fillStyle = axis[index] === selected ? '#20252b' : '#cdd3db';
          context.fillText(metric(value, 's', 1), bar.x, bar.y + 6);
        });
        context.restore();
      },
    };
    const chart = new Chart(canvas.current, {
      type: definition.type,
      data: {
        labels: axis.map((count) => {
          if (!overview || kind !== 'elapsed') return `${count}W`;
          return `${count} ${count === 1 ? 'worker' : 'workers'}`;
        }),
        datasets,
      },
      plugins: [valueLabels],
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        onClick: (_event, elements) => {
          const count = axis[elements[0]?.index ?? -1];
          if (count !== undefined) select(count);
        },
        plugins: {
          legend: {
            display: kind === 'speedup',
            labels: {
              color: '#bcc4ce',
              usePointStyle: true,
              font: { family: 'Inter', size: 10 },
            },
          },
          tooltip: {
            callbacks: {
              label: (item) =>
                `${item.dataset.label ?? definition.unit} · ${String(item.parsed.y)}`,
            },
          },
        },
        scales: {
          x: {
            border: { display: false },
            grid: {
              display: kind === 'cpu',
              color: '#262d35',
              drawTicks: false,
            },
            ticks: {
              color: (context) =>
                axis[context.index] === selected ? '#f2f4f6' : '#a4acb6',
              font: { family: 'Inter', size: 10.5, weight: 'normal' },
              padding: 8,
              maxRotation: 0,
            },
            title: {
              display: !overview,
              text: full
                ? 'Requested process-pool workers'
                : 'Process-pool workers',
              color: '#a4acb6',
            },
          },
          y: {
            beginAtZero: true,
            afterFit: (axis) => {
              if (overview && kind === 'elapsed')
                axis.width = Math.max(axis.width, 49);
            },
            suggestedMax: kind === 'cpu' ? 100 : undefined,
            border: { display: false },
            grid: { color: '#303640', drawTicks: false },
            ticks: {
              color: '#9aa3ae',
              count: 5,
              padding: overview && kind === 'elapsed' ? 0 : 11,
              align: overview && kind === 'elapsed' ? 'start' : 'center',
              font: {
                family: kind === 'elapsed' ? 'IBM Plex Mono' : 'Inter',
                size: 10,
              },
              callback: (value) => {
                if (overview && kind === 'elapsed') {
                  const seconds = Number(value);
                  if (seconds < 1)
                    return `${seconds.toLocaleString('en-US', { maximumSignificantDigits: 2 })}s`;
                  return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
                }
                return `${value}${kind === 'cpu' ? '%' : ''}${kind === 'elapsed' ? 's' : ''}`;
              },
            },
            title: {
              display: !overview,
              text: definition.unit,
              color: '#a4acb6',
            },
          },
        },
      },
    });
    const refreshFonts = () => chart.update('none');
    document.fonts?.addEventListener('loadingdone', refreshFonts);
    return () => {
      document.fonts?.removeEventListener('loadingdone', refreshFonts);
      chart.destroy();
    };
  }, [summaries, selected, select, kind, definition, overview, full]);
  if (!definition) return null;
  return (
    <ChartPanel
      kind={kind}
      selected={selected}
      summaries={summaries}
      overview={overview}
      full={full}
      resultsHref={resultsHref}
    >
      <div className="chart-canvas">
        <canvas
          ref={canvas}
          aria-hidden="true"
          aria-label={`${definition.title} by worker count`}
          aria-describedby="comparison-table-note"
        >
          See the measured results table for values and availability.
        </canvas>
      </div>
    </ChartPanel>
  );
}

export default function Charts(props: Props) {
  const definitions = props.overview
    ? chartDefinitions.slice(0, 2)
    : chartDefinitions;
  return (
    <div className={props.overview ? 'overview-charts' : 'comparison-charts'}>
      {definitions.map((definition) => (
        <MetricChart key={definition.id} {...props} kind={definition.id} />
      ))}
    </div>
  );
}
