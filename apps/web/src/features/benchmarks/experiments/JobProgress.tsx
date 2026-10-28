import { isTerminal, type Job } from '@/api/contracts';

import { timestamp } from '@/lib/format';

export default function JobProgress({
  job,
  busy,
  cancel,
  verified = true,
  recovering = false,
  pending = false,
  error = '',
  retryStatus,
}: {
  readonly job: Job | null;
  readonly busy: boolean;
  readonly cancel: () => Promise<void>;
  readonly verified?: boolean;
  readonly recovering?: boolean;
  readonly pending?: boolean;
  readonly error?: string;
  readonly retryStatus?: (() => void) | undefined;
}) {
  if (!job)
    return (
      <InactiveExperiment
        recovering={recovering}
        pending={pending}
        error={error}
        retryStatus={retryStatus}
      />
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
        <span className={`state ${verified ? job.state.toLowerCase() : ''}`}>
          {verified ? job.state : 'UNVERIFIED'}
        </span>
      </div>
      <output>
        {verified
          ? job.state
          : `Status unverified · last confirmed ${job.state}`}{' '}
        · {job.completed_batches} / {job.total_batches} batches completed
        {job.cancel_requested
          ? ' · Cancellation requested; the current batch must drain.'
          : ''}
      </output>
      <JobRequestError error={error} retryStatus={retryStatus} />
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
          {!verified && 'Last confirmed outcome: '}
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

function InactiveExperiment({
  recovering,
  pending,
  error,
  retryStatus,
}: {
  readonly recovering: boolean;
  readonly pending: boolean;
  readonly error: string;
  readonly retryStatus: (() => void) | undefined;
}) {
  let message =
    'Configure a dataset and start a real benchmark. Progress is reported at batch boundaries.';
  if (pending)
    message =
      'Submission acceptance is unconfirmed. Retry the same submission from the form to recover it safely.';
  if (recovering)
    message =
      'An accepted job is retained. Its current status could not yet be verified; retry status polling when the API is reachable.';
  return (
    <section className="card job-progress">
      <div className="panel-heading">
        <div>
          <h2>Job execution</h2>
          <p>
            {recovering || pending
              ? 'Saved experiment status is unverified'
              : 'No active experiment'}
          </p>
        </div>
        <span className="state">
          {recovering || pending ? 'UNVERIFIED' : 'IDLE'}
        </span>
      </div>
      <JobRequestError error={error} retryStatus={retryStatus} />
      <div className="job-idle">
        <h2>
          {recovering || pending ? 'Verification needed' : 'Ready to measure'}
        </h2>
        <p>{message}</p>
      </div>
      <div className="worker-activity">
        <h2>Worker Activity</h2>
        <p className="fine-print">
          The complete 1/2/4/8-worker matrix runs locally. Individual worker
          progress and live CPU usage are unavailable.
        </p>
      </div>
    </section>
  );
}

function JobRequestError({
  error,
  retryStatus,
}: {
  readonly error: string;
  readonly retryStatus: (() => void) | undefined;
}) {
  if (!error) return null;
  return (
    <p className="request-error">
      {error}
      {retryStatus && (
        <button type="button" onClick={retryStatus}>
          Resume status polling
        </button>
      )}
    </p>
  );
}
