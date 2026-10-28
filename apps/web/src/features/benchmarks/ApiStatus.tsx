import type { useApiHealth } from './useApiHealth';

export default function ApiStatus({
  health,
}: {
  readonly health: ReturnType<typeof useApiHealth>;
}) {
  const { status, checking, check } = health;
  return (
    <div className="stack">
      <section className="notice api-notice">
        <h2>HTTP job-submission latency vs image-processing completion time</h2>
        <p>
          Request acceptance and image completion measure different workflows.
          The API has no persisted HTTP load-test results. Connection checks do
          not start image benchmarks or establish available queue capacity.
        </p>
      </section>
      <div className="api-connection">
        <div>
          <span className="eyebrow">API connection</span>
          <output aria-live="polite">
            {checking ? 'Checking API connection…' : status}
          </output>
        </div>
        <button type="button" onClick={check} disabled={checking}>
          {checking ? 'Checking…' : 'Check connection'}
        </button>
      </div>
      <div className="api-metrics">
        {[
          ['Virtual Users', 'Concurrent HTTP clients'],
          ['Requests / sec', 'Job-submission ingestion rate'],
          ['HTTP Latency (Avg / p95)', 'Request acceptance latency'],
          ['Request Error Rate', 'HTTP 429 / 503 responses'],
        ].map(([title, note]) => (
          <section className="card api-metric" key={title}>
            <h2>{title}</h2>
            <div className="api-unavailable">Unavailable</div>
            <p className="fine-print">{note}</p>
          </section>
        ))}
      </div>
      <div className="api-charts">
        {[
          [
            'Request-Submission Throughput by Virtual Users',
            'HTTP job-enqueue rate (requests/sec)',
          ],
          [
            'HTTP Ingestion Latency: Avg vs p95 (ms)',
            'Request acceptance time by concurrency level',
          ],
        ].map(([title, note]) => (
          <section className="card chart-card" key={title}>
            <div className="panel-heading">
              <div>
                <h2>{title}</h2>
                <p>{note}</p>
              </div>
            </div>
            <div className="chart-empty api-chart-empty">
              No HTTP load-test measurements available
            </div>
          </section>
        ))}
      </div>
      <section className="card">
        <div className="panel-heading">
          <div>
            <h2>Load testing unavailable</h2>
            <p>
              Image throughput is shown in completed images/second in Compare.
            </p>
          </div>
        </div>
        <div className="table-scroll">
          <table>
            <caption className="sr-only">
              HTTP load-test telemetry availability
            </caption>
            <thead>
              <tr>
                {[
                  'Virtual Users',
                  'Submission Rate (req/s)',
                  'Avg HTTP Latency',
                  'p95 HTTP Latency',
                  'Request Error Rate',
                  'Queue Depth',
                ].map((label) => (
                  <th key={label} scope="col">
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr>
                <td colSpan={6} className="empty">
                  No persisted HTTP load tests. Virtual users, latency
                  percentiles, request rates and queue telemetry are
                  unavailable.
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
