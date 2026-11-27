import type { WorkerSummary } from '@/api/contracts';
import { metric } from '@/lib/format';

const availableRuns = (count: number) =>
  `${count} ${count === 1 ? 'run' : 'runs'}`;

export default function ResultsTable({
  summaries,
  selected,
  select,
}: {
  readonly summaries: readonly WorkerSummary[];
  readonly selected: number;
  readonly select: (count: number) => void;
}) {
  return (
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
              <td>
                {summary.measured_count}
                {!summary.complete && <small> · partial</small>}
              </td>
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
                <small>({availableRuns(summary.cpu_runs_available)})</small>
              </td>
              <td>
                {metric(summary.rss_peak_mib)}{' '}
                <small>({availableRuns(summary.memory_runs_available)})</small>
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
  );
}
