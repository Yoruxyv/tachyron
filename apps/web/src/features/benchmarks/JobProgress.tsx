import { isTerminal, type Job } from '@/api/contracts';

import { timestamp } from './format';

export default function JobProgress({
  job,
  busy,
  cancel,
}: {
  readonly job: Job | null;
  readonly busy: boolean;
  readonly cancel: () => Promise<void>;
}) {
  if (!job)
    return (
      <section className="card empty">
        <h2>No active experiment</h2>
        <p>
          Configure a dataset and start a real benchmark. Progress is reported
          at batch boundaries.
        </p>
      </section>
    );
  const terminal = isTerminal(job.state);
  const failed = job.state === 'FAILED' || job.state === 'CANCELLED';
  return (
    <section className="card job-progress" aria-labelledby="job-progress-title">
      <div className="panel-heading">
        <div>
          <h2 id="job-progress-title">Job execution</h2>
          <p className="mono">{job.id}</p>
        </div>
        <span className={`state ${job.state.toLowerCase()}`}>{job.state}</span>
      </div>
      <output>
        {job.state} · {job.completed_batches} / {job.total_batches} batches
        completed
        {job.cancel_requested
          ? ' · Cancellation requested; the current batch must drain.'
          : ''}
      </output>
      <progress
        value={job.completed_batches}
        max={job.total_batches}
        aria-label="Completed benchmark batches"
      />
      <dl className="job-accounting">
        <div>
          <dt>Successful attempts</dt>
          <dd>{job.succeeded_images}</dd>
        </div>
        <div>
          <dt>Failed attempts</dt>
          <dd>{job.failed_images}</dd>
        </div>
        <div>
          <dt>Unknown batches</dt>
          <dd>{job.unknown_batches}</dd>
        </div>
      </dl>
      <p className="fine-print">
        Image attempts include warm-ups and repeated images. Individual worker
        progress and live CPU usage are unavailable.
      </p>
      {failed && (
        <p role="alert" className="error">
          {job.state === 'FAILED'
            ? 'The experiment failed.'
            : 'The experiment was cancelled.'}{' '}
          Complete scaling results are unavailable. Server code:{' '}
          <code>{job.error_code ?? 'none'}</code>.
        </p>
      )}
      <p className="fine-print">
        Accepted {timestamp(job.created_at)}
        {job.ended_at && ` · Ended ${timestamp(job.ended_at)}`}
      </p>
      {!terminal && (
        <button
          type="button"
          disabled={busy || job.cancel_requested}
          onClick={cancel}
        >
          {job.cancel_requested
            ? 'Cancellation requested'
            : 'Cancel experiment'}
        </button>
      )}
    </section>
  );
}
