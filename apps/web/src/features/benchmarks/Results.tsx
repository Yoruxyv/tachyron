import { lazy, Suspense } from 'react';

import { type HistoryDetail, type WorkerCount } from '@/api/contracts';
import Icon from '@/components/Icon';
import ChartBoundary from '@/components/ChartBoundary';

import { chartDefinitions } from './chartData';
import ChartPanel from './ChartPanel';
import { metric, timestamp } from './format';
import WorkerSelector from './WorkerSelector';

const Charts = lazy(() => import('./Charts'));
interface Props {
  readonly detail: HistoryDetail | null;
  readonly selected: WorkerCount;
  readonly select: (count: WorkerCount) => void;
  readonly overview?: boolean;
}

export function Kpis({ detail, selected }: Pick<Props, 'detail' | 'selected'>) {
  const summaries =
    detail?.job.state === 'COMPLETED' &&
    detail.result?.experiment.status === 'success'
      ? detail.result.summaries
      : undefined;
  const summary = summaries?.find(
    (item) => item.workers === selected && item.complete,
  );
  const values = [
    {
      icon: 'time',
      label: 'Execution time',
      value: metric(summary?.elapsed?.mean, '', 1),
      unit:
        summary?.elapsed?.mean === null || summary?.elapsed?.mean === undefined
          ? ''
          : ' s',
      note: `Mean of ${summary?.measured_count ?? 0} measured runs`,
    },
    {
      icon: 'speed',
      label: 'Parallel speedup',
      value: metric(summary?.speedup, '×', 2, 2),
      unit: '',
      note: 'Against the 1-worker process pool',
    },
    {
      icon: 'cpu',
      label: 'CPU utilization',
      value: metric(summary?.cpu_mean_core_percent, '%'),
      unit: '',
      note: '% of one logical core · sampled tree',
    },
    {
      icon: 'efficiency',
      label: 'Scaling efficiency',
      value: metric(summary?.efficiency_percent, '%', 1),
      unit: '',
      note: `Relative to ideal ${selected}× scaling`,
    },
  ] as const;
  return (
    <div className="kpi-grid">
      {values.map((item) => (
        <section className="card kpi" key={item.label} aria-label={item.label}>
          <h2>
            <Icon name={item.icon} />
            {item.label}
          </h2>
          <div className="kpi-number">
            {item.value}
            <span>{item.unit}</span>
          </div>
          <p>{item.note}</p>
        </section>
      ))}
    </div>
  );
}

export default function Results({
  detail,
  selected,
  select,
  overview = false,
}: Props) {
  const valid =
    detail?.job.state === 'COMPLETED' &&
    detail.result?.experiment.status === 'success';
  const summaries = valid ? (detail.result?.summaries ?? []) : [];
  const environment = valid
    ? detail.result?.experiment.context.environment
    : null;
  const definitions = overview
    ? chartDefinitions.slice(0, 2)
    : chartDefinitions;
  const measurementTable = valid ? (
    <section className="card results-table">
      <h2>Measured results</h2>
      <p id="comparison-table-note" className="fine-print">
        Warm-ups excluded. Mean ± sample standard deviation (ddof = 1);
        unavailable observations stay unavailable. Ideal speedup is theoretical.
      </p>
      <div className="table-scroll" aria-label="Measured results table">
        <table>
          <caption className="sr-only">
            Measured metrics and spread by worker count
          </caption>
          <thead>
            <tr>
              <th scope="col">Workers</th>
              <th scope="col">Runs</th>
              <th scope="col">Time mean ± SD (s)</th>
              <th scope="col">Min–max (s)</th>
              <th scope="col">Completed img/s</th>
              <th scope="col">CPU (% of 1 core)</th>
              <th scope="col">Peak RSS (MiB)</th>
              <th scope="col">Speedup / ideal</th>
              <th scope="col">Efficiency</th>
            </tr>
          </thead>
          <tbody>
            {summaries.map((summary) => (
              <tr
                key={summary.workers}
                data-selected={summary.workers === selected}
              >
                <th scope="row">
                  <button
                    type="button"
                    aria-pressed={summary.workers === selected}
                    onClick={() => select(summary.workers)}
                  >
                    {summary.workers}W
                  </button>
                </th>
                <td>{summary.measured_count}</td>
                <td>
                  {metric(summary.elapsed?.mean)} ±{' '}
                  {metric(summary.elapsed?.sample_stddev)}
                </td>
                <td>
                  {metric(summary.elapsed?.minimum)}–
                  {metric(summary.elapsed?.maximum)}
                </td>
                <td>{metric(summary.mean_throughput)}</td>
                <td>
                  {metric(summary.cpu_mean_core_percent)}{' '}
                  <small>({summary.cpu_runs_available} runs)</small>
                </td>
                <td>
                  {metric(summary.rss_peak_mib)}{' '}
                  <small>({summary.memory_runs_available} runs)</small>
                </td>
                <td>
                  {metric(summary.speedup, '×')} / {summary.workers}×
                </td>
                <td>{metric(summary.efficiency_percent, '%')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="fine-print">
        CPU: observed process tree; 100% equals one fully used logical core. RSS
        is sampled and may miss short-lived peaks. Spawn startup and teardown
        are included in elapsed time.
      </p>
      <p className="fine-print">
        {environment?.cpu_model ?? 'CPU model unavailable'} ·{' '}
        {environment?.logical_cpus ?? 'Unknown'} logical CPUs ·{' '}
        {environment?.os_name} · Python {environment?.python_version}
      </p>
      <p className="fine-print mono">Context {detail.result?.context_id}</p>
    </section>
  ) : null;
  return (
    <>
      {!overview && (
        <div className="results-toolbar">
          <div>
            <p className="eyebrow">
              {detail
                ? `${detail.job.request.dataset_id} · ${detail.job.input_count} images`
                : 'Measured scaling'}
            </p>
            <p className="fine-print">
              {detail
                ? `Accepted ${timestamp(detail.job.created_at)} · ${detail.job.request.width} × ${detail.job.request.height} px`
                : 'Choose a saved experiment or run your first benchmark.'}
            </p>
          </div>
          <WorkerSelector value={selected} onChange={select} />
        </div>
      )}
      {detail?.job.request.tiny && (
        <p className="notice">
          Tiny functional test. These timings verify functionality and are not
          representative performance evidence.
        </p>
      )}
      {!overview && measurementTable}
      {valid ? (
        <ChartBoundary>
          <Suspense fallback={<output>Loading charts…</output>}>
            <Charts
              summaries={summaries}
              selected={selected}
              select={select}
              overview={overview}
            />
          </Suspense>
        </ChartBoundary>
      ) : (
        <div className={overview ? 'overview-charts' : 'comparison-charts'}>
          {definitions.map((definition) => (
            <ChartPanel
              key={definition.id}
              kind={definition.id}
              selected={selected}
              summaries={[]}
              overview={overview}
            >
              <div className="chart-canvas chart-empty">
                No complete measurements yet<span>{definition.unit}</span>
              </div>
            </ChartPanel>
          ))}
        </div>
      )}
      {valid && overview && (
        <details className="results-details">
          <summary>Measured values and sample spread</summary>
          {measurementTable}
        </details>
      )}
      {detail && !valid && (
        <p className="notice">
          {detail.job.state}: complete comparison metrics are unavailable.
          Partial evidence may be inspected in the raw API record.
        </p>
      )}
    </>
  );
}
