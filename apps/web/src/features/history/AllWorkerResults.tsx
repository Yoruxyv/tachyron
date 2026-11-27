import { lazy, Suspense, useState } from 'react';

import type { HistoryDetail } from '@/api/contracts';
import ChartBoundary from '@/components/ChartBoundary';
import Select from '@/components/Select';
import { comparisonHref } from '@/components/useNavigation';
import {
  chartDefinitions,
  chartValues,
  inspectedCount,
  measuredCounts,
  type ChartMetric,
} from '@/features/benchmarks/results/chartData';
import ResultsTable from '@/features/benchmarks/results/ResultsTable';
import { metric, timestamp } from '@/lib/format';

import type { useHistory } from './hooks/useHistory';
import './all-worker-results.css';

const MetricChart = lazy(() =>
  import('@/features/benchmarks/results/Charts').then(({ MetricChart }) => ({
    default: MetricChart,
  })),
);
const metricOptions = chartDefinitions.map(({ id, title }) => ({
  value: id,
  label: title,
}));

function EvidenceHeader({ detail }: { readonly detail: HistoryDetail }) {
  const { job, result } = detail;
  const environment = result?.experiment.context.environment;
  const source = job.request.worker_counts
    ? 'Custom benchmark v2'
    : 'Benchmark v1';
  return (
    <section
      className="card worker-results-source"
      aria-labelledby="worker-evidence-heading"
    >
      <div className="panel-heading">
        <div>
          <h2 id="worker-evidence-heading">{job.request.dataset_id}</h2>
          <p>
            {job.input_count} images · Resize + Grayscale · {job.request.width}{' '}
            × {job.request.height} px
          </p>
        </div>
        <span className="badge">{source}</span>
      </div>
      <dl className="worker-evidence">
        <div>
          <dt>Saved job</dt>
          <dd className="mono">{job.id}</dd>
        </div>
        {result?.experiment.experiment_id && (
          <div>
            <dt>Experiment</dt>
            <dd className="mono">{result.experiment.experiment_id}</dd>
          </div>
        )}
        <div>
          <dt>Outcome</dt>
          <dd>
            {job.state} · Accepted {timestamp(job.created_at)}
          </dd>
        </div>
        {environment && (
          <div>
            <dt>Recorded environment</dt>
            <dd>
              {environment.cpu_model ?? 'CPU model unavailable'} ·{' '}
              {environment.logical_cpus ?? 'Unknown'} logical CPUs ·{' '}
              {environment.os_name} · Python {environment.python_version}
            </dd>
          </div>
        )}
        {result && (
          <div>
            <dt>Context</dt>
            <dd className="mono">{result.context_id}</dd>
          </div>
        )}
      </dl>
      <a
        className="worker-results-link"
        href={`/api/benchmarks/${encodeURIComponent(job.id)}`}
        target="_blank"
        rel="noreferrer"
      >
        Open raw API evidence <span aria-hidden="true">↗</span>
      </a>
    </section>
  );
}

function FullComparison({
  detail,
  selected: preferred,
  select,
}: {
  readonly detail: HistoryDetail;
  readonly selected: number;
  readonly select: (count: number) => void;
}) {
  const [kind, setKind] = useState<ChartMetric>('elapsed');
  const [sortByValue, setSortByValue] = useState(false);
  const summaries = detail.result?.summaries ?? [];
  const counts = measuredCounts(summaries);
  const selected = inspectedCount(counts, preferred);
  const included = summaries.filter((item) => counts.includes(item.workers));
  const tableRows = sortByValue
    ? [...included].sort((left, right) => {
        const a =
          chartValues([left], kind, [left.workers], true)[0] ?? Infinity;
        const b =
          chartValues([right], kind, [right.workers], true)[0] ?? Infinity;
        return a === b ? left.workers - right.workers : a - b;
      })
    : included;
  const selectedRow = included.find((item) => item.workers === selected);

  if (!counts.length)
    return (
      <p className="notice">
        No measured repetitions are available for this benchmark.
      </p>
    );

  return (
    <>
      <div className="worker-results-controls">
        <div className="form-field">
          <label htmlFor="worker-results-metric">Chart metric</label>
          <Select
            id="worker-results-metric"
            label="Chart metric"
            value={kind}
            options={metricOptions}
            onChange={(value) => {
              const option = chartDefinitions.find(
                (definition) => definition.id === value,
              );
              if (option) setKind(option.id);
            }}
          />
        </div>
        {selectedRow && (
          <p className="fine-print">
            Inspecting {selected}W · {selectedRow.measured_count} measured{' '}
            {selectedRow.measured_count === 1 ? 'round' : 'rounds'} ·{' '}
            {metric(selectedRow.elapsed?.mean, ' s')} mean wall time
          </p>
        )}
      </div>
      <div className="worker-chart-scroll">
        <div style={{ minWidth: Math.max(0, counts.length - 3) * 79 + 160 }}>
          <ChartBoundary>
            <Suspense fallback={<output>Loading chart…</output>}>
              <MetricChart
                kind={kind}
                selected={selected}
                select={select}
                summaries={included}
                full
              />
            </Suspense>
          </ChartBoundary>
        </div>
      </div>
      <section className="card results-table">
        <div className="panel-heading">
          <h2>Measured results</h2>
          <button
            type="button"
            aria-pressed={sortByValue}
            onClick={() => setSortByValue((current) => !current)}
          >
            Sort by chart metric
          </button>
        </div>
        <p id="comparison-table-note" className="fine-print">
          Values exclude warm-ups. Spread is sample standard deviation. Missing
          values are labeled unavailable.
        </p>
        <ResultsTable
          summaries={tableRows}
          selected={selected}
          select={select}
        />
        <p className="fine-print">
          Worker counts are requested pool sizes. Elapsed time includes pool
          startup and teardown. Throughput is the mean of per-run image rates.
          CPU is sampled across the process tree, where 100% equals one logical
          core. RSS sums observed resident memory and can miss short peaks.
          Speedup and efficiency use the measured one-worker pool baseline when
          comparable evidence is available.
        </p>
      </section>
    </>
  );
}

export default function AllWorkerResults({
  id,
  history,
  selected,
  select,
}: {
  readonly id: string | null;
  readonly history: ReturnType<typeof useHistory>;
  readonly selected: number;
  readonly select: (count: number) => void;
}) {
  const detail = id && history.detail?.job.id === id ? history.detail : null;
  return (
    <div className="stack worker-results">
      <a
        className="worker-results-link"
        href={id ? comparisonHref(id) : '#compare'}
      >
        ← Back to Compare
      </a>
      {!id && (
        <p className="notice">
          Choose a saved benchmark from Compare to view its worker results.
        </p>
      )}
      {id && history.detailError && (
        <p className="request-error">
          {history.detailError}{' '}
          <button type="button" onClick={history.reload}>
            Retry evidence
          </button>
        </p>
      )}
      {id && !detail && !history.detailError && (
        <output>Loading experiment evidence…</output>
      )}
      {detail && !history.detailError && (
        <>
          <EvidenceHeader detail={detail} />
          {detail.job.request.tiny && (
            <p className="notice">
              Tiny functional test. Timings verify functionality and are not
              representative performance evidence.
            </p>
          )}
          {detail.job.state !== 'COMPLETED' && (
            <p className="notice">
              This benchmark ended {detail.job.state.toLowerCase()}. Only
              available measurements are shown.
            </p>
          )}
          {detail.result ? (
            <FullComparison
              key={detail.job.id}
              detail={detail}
              selected={selected}
              select={select}
            />
          ) : (
            <p className="notice">Saved benchmark evidence is unavailable.</p>
          )}
        </>
      )}
    </div>
  );
}
