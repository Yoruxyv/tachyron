import { useEffect, useRef, useState } from 'react';

import { getHealth } from '@/api/health';

export default function ApiStatus() {
  const [status, setStatus] = useState('Not checked');
  const [checking, setChecking] = useState(false);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  async function check() {
    if (request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setChecking(true);
    setStatus('Checking API connection…');
    try {
      await getHealth(controller.signal);
      if (!controller.signal.aborted) setStatus('API online');
    } catch {
      if (!controller.signal.aborted)
        setStatus(
          'API unavailable. Check that the local server is running, then retry.',
        );
    } finally {
      request.current = null;
      if (!controller.signal.aborted) setChecking(false);
    }
  }
  return (
    <div className="stack">
      <section className="card">
        <h2>API connection</h2>
        <p>
          Liveness does not establish benchmark readiness or available queue
          capacity.
        </p>
        <output>{status}</output>
        <button type="button" onClick={check} disabled={checking}>
          {checking ? 'Checking…' : 'Check connection'}
        </button>
      </section>
      <section className="card empty">
        <h2>Load testing unavailable</h2>
        <p>
          The API has no persisted HTTP load-test results. Requests/second,
          virtual users, latency percentiles, and per-worker live activity are
          unavailable.
        </p>
        <p>
          Image throughput is shown in completed images/second in Compare.
          Connection checks do not start image benchmarks.
        </p>
      </section>
    </div>
  );
}
