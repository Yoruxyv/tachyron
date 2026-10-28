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
} from 'chart.js';

import { workers, type WorkerCount, type WorkerSummary } from '@/api/contracts';

import { chartDefinitions, chartValues, type ChartMetric } from './chartData';

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
  readonly selected: WorkerCount;
  readonly select: (count: WorkerCount) => void;
  readonly overview?: boolean;
}
/** Own one Chart.js instance; destroy it before replacing its data or selection. */
function MetricChart({
  summaries,
  selected,
  select,
  kind,
}: Props & { readonly kind: ChartMetric }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const definition = chartDefinitions.find((item) => item.id === kind);
  useEffect(() => {
    if (!canvas.current || !definition) return;
    const values = chartValues(summaries, kind);
    const datasets: ChartDataset<'bar' | 'line', (number | null)[]>[] = [
      {
        label: definition.unit,
        data: values,
        backgroundColor: workers.map((count) =>
          count === selected ? '#e9edf2' : '#66717f',
        ),
        borderColor: '#d7dbe1',
        borderWidth: definition.type === 'line' ? 2 : 0,
        borderRadius: 4,
        pointRadius: workers.map((count) => (count === selected ? 6 : 4)),
      },
    ];
    if (kind === 'speedup')
      datasets.push({
        label: 'Theoretical ideal',
        type: 'line',
        data: [...workers],
        borderColor: '#8e98a6',
        borderDash: [5, 5],
        pointStyle: 'triangle',
        pointRadius: 4,
        backgroundColor: '#8e98a6',
      });
    const chart = new Chart(canvas.current, {
      type: definition.type,
      data: { labels: workers.map((count) => `${count}W`), datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        onClick: (_event, elements) => {
          const count = workers[elements[0]?.index ?? -1];
          if (count !== undefined) select(count);
        },
        plugins: {
          legend: {
            display: kind === 'speedup',
            labels: { color: '#bcc4ce', usePointStyle: true },
          },
          tooltip: { enabled: true },
        },
        scales: {
          x: {
            grid: { display: false },
            ticks: { color: '#a4acb6' },
            title: {
              display: true,
              text: 'Process-pool workers',
              color: '#a4acb6',
            },
          },
          y: {
            beginAtZero: true,
            grid: { color: '#2b323b' },
            ticks: { color: '#a4acb6' },
            title: { display: true, text: definition.unit, color: '#a4acb6' },
          },
        },
      },
    });
    return () => chart.destroy();
  }, [summaries, selected, select, kind, definition]);
  if (!definition) return null;
  return (
    <section className="card chart-card" aria-labelledby={`chart-${kind}`}>
      <div className="panel-heading">
        <div>
          <h2 id={`chart-${kind}`}>{definition.title}</h2>
          <p>{definition.note}</p>
        </div>
        <span className="badge">
          {kind === 'cpu' ? 'Sampled CPU' : 'Measured'}
        </span>
      </div>
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
      <p className="fine-print">
        {chartValues(summaries, kind).every((value) => value === null)
          ? 'No observations available for this metric.'
          : 'Select a point or bar to inspect a worker count.'}
      </p>
    </section>
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
