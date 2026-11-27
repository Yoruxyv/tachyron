import { lazy, Suspense } from 'react';

import { workers, type HistoryDetail } from '@/api/contracts';
import Icon from '@/components/Icon';
import ChartBoundary from '@/components/ChartBoundary';
import { comparisonHref } from '@/components/useNavigation';

import { chartDefinitions, inspectedCount, measuredCounts } from './chartData';
import ChartPanel from './ChartPanel';
import { metric, timestamp } from '@/lib/format';
import WorkerSelector from './WorkerSelector';
import ResultsTable from './ResultsTable';

const Charts = lazy(() => import('./Charts'));
interface Props {
  readonly detail: HistoryDetail | null;
  readonly selected: number;
  readonly select: (count: number) => void;
  readonly overview?: boolean;
}

export function Kpis({
  detail,
  selected: preferred,
}: Pick<Props, 'detail' | 'selected'>) {
  const summaries =
    detail?.job.state === 'COMPLETED' &&
    detail.result?.experiment.status === 'success'
      ? detail.result.summaries
      : undefined;
  const selected = inspectedCount(measuredCounts(summaries ?? []), preferred);
  const summary = summaries?.find(
    (item) => item.workers === selected && item.complete,
  );
  const runCount = summary?.measured_count ?? 0;
  const baselineMissing =
    Boolean(summaries) &&
    !summaries?.some((item) => item.workers === 1 && item.complete);
  const values = [
    {
      icon: 'time',
      label: 'Execution time',
      value: metric(summary?.elapsed?.mean, '', 1),
      unit:
        summary?.elapsed?.mean === null || summary?.elapsed?.mean === undefined
          ? ''
          : ' s',
      note: `Mean of ${runCount} measured ${runCount === 1 ? 'run' : 'runs'}`,
    },
    {
      icon: 'speed',
      label: 'Parallel speedup',
      value: metric(summary?.speedup, '×', 2, 2),
      unit: '',
      note: baselineMissing
        ? 'One-worker baseline unavailable'
        : 'Against the 1-worker process pool',
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
      note: baselineMissing
        ? 'One-worker baseline unavailable'
        : `Relative to ideal ${selected}× scaling`,
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
  selected: preferred,
  select,
  overview = false,
}: Props) {
  const valid =
    detail?.job.state === 'COMPLETED' &&
    detail.result?.experiment.status === 'success';
  const summaries = valid ? (detail.result?.summaries ?? []) : [];
  const counts = measuredCounts(summaries);
  const selected = inspectedCount(counts, preferred);
  const resultsHref = detail?.result
    ? comparisonHref(detail.job.id, true)
    : undefined;
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
      <ResultsTable summaries={summaries} selected={selected} select={select} />
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
          <WorkerSelector
            value={selected}
            counts={counts.length ? counts : workers}
            onChange={select}
          />
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
              resultsHref={resultsHref}
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
              resultsHref={resultsHref}
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
