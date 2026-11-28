import { useState } from 'react';

import {
  isOperationTerminal,
  type OperationEvent,
  type OperationProgress,
  type OperationSnapshot,
} from '@/api/operations';
import Icon from '@/components/Icon';
import Select from '@/components/Select';

import TaskObservations from './TaskObservations';

const eventLabels: Readonly<Partial<Record<string, string>>> = {
  operation_queued: 'Run queued',
  operation_started: 'Run started',
  calibration_started: 'Calibration started',
  selection_decided: 'Adaptive decision recorded',
  pool_admitted: 'Worker pool admitted',
  pool_terminal: 'Pool terminal outcome recorded',
  worker_observations_unavailable: 'Worker observations unavailable',
  cancel_requested: 'Cancellation requested',
  operation_terminal: 'Run terminal outcome recorded',
  execution_started: 'Execution started',
  execution_terminal: 'Execution terminal outcome recorded',
  worker_started: 'Worker startup observed',
  worker_ready: 'Worker readiness observed',
  worker_exited: 'Worker exit observed',
  task_submitted: 'Task submitted',
  submission_unknown: 'Task submission unconfirmed',
  task_started: 'Task start observed',
  task_result_received: 'Task result received',
  task_succeeded: 'Success acknowledged',
  task_failed: 'Failure acknowledged',
  task_unknown: 'Task outcome unknown',
  task_not_started: 'Task classified as not started',
};

const workerEventTypes = new Set([
  'worker_started',
  'worker_ready',
  'worker_exited',
  'task_started',
  'task_result_received',
  'task_succeeded',
  'task_failed',
]);

const lifecycleEvents = [
  { type: 'worker_started', label: 'Startup' },
  { type: 'worker_ready', label: 'Readiness' },
  { type: 'worker_exited', label: 'Exit' },
] as const;

function WorkerLifecycle({
  events,
}: {
  readonly events: readonly OperationEvent[];
}) {
  return (
    <ul
      className="monitor-worker-lifecycle"
      aria-label="Retained lifecycle observations"
    >
      {lifecycleEvents.map(({ type, label: stage }) => {
        const observed = events.some((event) => event.type === type);
        return (
          <li key={type} data-observed={observed}>
            {stage} <span>{observed ? 'observed' : 'event unavailable'}</span>
          </li>
        );
      })}
    </ul>
  );
}

const workerLabels = {
  OBSERVED: 'Observed',
  STARTED: 'Started',
  READY: 'Ready',
  EXITED: 'Exited',
};

const phaseLabels = {
  queued: 'Queued',
  preparing: 'Preparing',
  calibration: 'Calibration',
  execution: 'Final processing',
};

export function OperationPhase({
  phase,
}: {
  readonly phase: OperationSnapshot['phase'];
}) {
  return phaseLabels[phase];
}

const outcomeLabels = {
  succeeded: 'Succeeded',
  failed: 'Failed',
  unknown: 'Unknown',
  not_started: 'Not started',
  pending: 'Pending',
} as const;

function label(event: OperationEvent) {
  return Object.hasOwn(eventLabels, event.type)
    ? eventLabels[event.type]
    : 'Execution observation';
}

function readableCode(code: string) {
  return code.replaceAll('_', ' ');
}

function isFresh(event: OperationEvent, animateAfter: number | null) {
  return animateAfter !== null && event.sequence > animateAfter;
}

function FinalProgress({ progress }: { readonly progress: OperationProgress }) {
  const acknowledged = progress.succeeded + progress.failed;
  return (
    <section
      className="monitor-panel"
      aria-labelledby="monitor-progress-heading"
    >
      <h3 id="monitor-progress-heading">Final processing</h3>
      <p className="monitor-count">
        <strong>{acknowledged.toLocaleString()}</strong>
        <span> / {progress.total.toLocaleString()} outcomes acknowledged</span>
      </p>
      {progress.total > 0 && (
        <progress
          aria-label="Final image outcomes acknowledged"
          max={progress.total}
          value={acknowledged}
        />
      )}
      <dl className="monitor-outcomes">
        {Object.entries(outcomeLabels).map(([key, name]) => (
          <div key={key}>
            <dt>{name}</dt>
            <dd>
              {progress[key as keyof typeof outcomeLabels].toLocaleString()}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function AdaptiveDetails({
  snapshot,
}: {
  readonly snapshot: OperationSnapshot;
}) {
  const { selection, pool, calibration_trials_finished: finished } = snapshot;
  const trial = pool?.trial;
  return (
    <section
      className="monitor-panel monitor-adaptive"
      aria-labelledby="monitor-adaptive-heading"
    >
      <h3 id="monitor-adaptive-heading">Adaptive calibration</h3>
      <div>
        <p>
          {finished.toLocaleString()} {finished === 1 ? 'trial' : 'trials'}{' '}
          finished
        </p>
        {pool && trial && (
          <p>
            Latest trial #{trial.index + 1} · Repetition {trial.repetition + 1}{' '}
            ·{' '}
            {(pool.progress.succeeded + pool.progress.failed).toLocaleString()}{' '}
            / {pool.progress.total.toLocaleString()} trial outcomes acknowledged
          </p>
        )}
      </div>
      <div className="monitor-adaptive-decision">
        {selection ? (
          <>
            <strong data-fallback={selection.fallback}>
              {selection.fallback ? 'Fallback' : 'Selected'} ·{' '}
              {selection.workers}{' '}
              {selection.workers === 1 ? 'worker' : 'workers'}
            </strong>
            <p>
              {readableCode(selection.reason)} ·{' '}
              {selection.profiled_inputs.toLocaleString()} inputs profiled
            </p>
          </>
        ) : (
          <p>
            {isBeforeDecision(snapshot.state)
              ? 'Awaiting a selection decision.'
              : 'No selection decision observed.'}
          </p>
        )}
      </div>
    </section>
  );
}

function isBeforeDecision(state: OperationSnapshot['state']) {
  return state === 'QUEUED' || state === 'RUNNING';
}

function WorkerPool({
  snapshot,
  animateAfter,
}: {
  readonly snapshot: OperationSnapshot;
  readonly animateAfter: number | null;
}) {
  const { pool } = snapshot;
  const requested =
    pool?.requested_workers ??
    snapshot.selection?.workers ??
    snapshot.requested_workers;
  // One verified snapshot scopes worker identities to the latest admitted pool.
  const poolEvents = pool
    ? snapshot.events.filter(
        (event) =>
          typeof event.execution_id === 'string' &&
          event.execution_id === pool.execution_id &&
          workerEventTypes.has(event.type),
      )
    : [];
  return (
    <section
      className="monitor-panel monitor-workers"
      aria-labelledby="monitor-workers-heading"
    >
      <div className="monitor-panel-heading">
        <h3 id="monitor-workers-heading">Worker pool</h3>
        {pool && <span>{pool.trial ? 'Calibration pool' : 'Final pool'}</span>}
      </div>
      <p className="monitor-count">
        <strong>{pool?.observed_workers.length ?? 0}</strong>
        <span> confirmed · {requested ?? '—'} requested</span>
      </p>
      {pool?.observed_workers.length ? (
        <>
          <ul
            className="monitor-worker-list"
            aria-label="Confirmed worker instances"
          >
            {pool.observed_workers.map((worker) => {
              const events = poolEvents.filter(
                (event) => event.instance_id === worker.instance_id,
              );
              const lastTask = events.findLast((event) => event.index !== null);
              const latest = events.at(-1);
              return (
                <li
                  key={worker.instance_id}
                  className="monitor-worker"
                  data-state={worker.state}
                  data-abnormal-exit={
                    worker.exit_code !== null && worker.exit_code !== 0
                  }
                >
                  {latest && isFresh(latest, animateAfter) && (
                    <span
                      key={latest.sequence}
                      className="monitor-worker-pulse"
                      aria-hidden="true"
                    />
                  )}
                  <span className="monitor-worker-icon">
                    <Icon name="cpu" />
                  </span>
                  <div className="monitor-worker-copy">
                    <div>
                      <strong>PID {worker.pid}</strong>{' '}
                      <span
                        className="monitor-worker-state"
                        data-state={worker.state}
                      >
                        {workerLabels[worker.state]}
                      </span>
                    </div>
                    <WorkerLifecycle events={events} />
                    {worker.exit_code !== null && (
                      <p>Exit code {worker.exit_code}</p>
                    )}
                    {lastTask && (
                      <p>
                        Last task observation · #{lastTask.index} ·{' '}
                        {label(lastTask)}
                      </p>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
          <p className="monitor-note">
            Ready means initialization was observed. Current assignment may be
            unknown.
          </p>
        </>
      ) : (
        <p className="monitor-note">
          {pool
            ? 'No worker instances have been confirmed for this pool.'
            : 'No pool has been admitted.'}
        </p>
      )}
    </section>
  );
}

function RecentEvents({ snapshot }: { readonly snapshot: OperationSnapshot }) {
  const [filter, setFilter] = useState('all');
  const events = snapshot.events
    .filter((event) => {
      if (filter === 'workers') return event.type.startsWith('worker_');
      if (filter === 'tasks') return event.index !== null;
      if (filter === 'calibration') return event.phase === 'calibration';
      return true;
    })
    .slice(-10)
    .reverse();
  return (
    <section
      className="monitor-panel monitor-events"
      aria-labelledby="monitor-events-heading"
    >
      <div className="monitor-panel-heading">
        <h3 id="monitor-events-heading">Recent observations</h3>
        <span>Sequence #{snapshot.snapshot_sequence}</span>
      </div>
      <div className="monitor-event-filter">
        <label htmlFor="monitor-event-filter">Show</label>
        <Select
          id="monitor-event-filter"
          label="Show"
          value={filter}
          onChange={setFilter}
          options={[
            { value: 'all', label: 'All observations' },
            { value: 'workers', label: 'Worker lifecycle' },
            { value: 'tasks', label: 'Task observations' },
            ...(snapshot.mode === 'adaptive'
              ? [{ value: 'calibration', label: 'Calibration' }]
              : []),
          ]}
        />
      </div>
      {events.length ? (
        <ol className="monitor-event-list" aria-label="Recent execution events">
          {events.map((event) => (
            <li key={event.sequence}>
              <span className="monitor-event-sequence">#{event.sequence}</span>
              <div>
                <strong>{label(event)}</strong>
                <p>
                  {typeof event.trial_index !== 'number'
                    ? phaseLabels[event.phase]
                    : `Calibration trial #${event.trial_index + 1}`}
                  {event.index !== null && ` · Task #${event.index}`}
                  {workerEventTypes.has(event.type) &&
                    typeof event.pid === 'number' &&
                    ` · PID ${event.pid}`}
                  {typeof event.exit_code === 'number' &&
                    ` · Exit code ${event.exit_code}`}
                </p>
              </div>
            </li>
          ))}
        </ol>
      ) : (
        <p className="monitor-note">No recent events available.</p>
      )}
    </section>
  );
}

function Coverage({ snapshot }: { readonly snapshot: OperationSnapshot }) {
  return (
    <aside className="monitor-coverage" aria-label="Telemetry coverage">
      <strong>
        {snapshot.coverage === 'best_effort' && 'Best-effort observations'}
        {snapshot.coverage === 'best_effort_with_gaps' &&
          'Best-effort observations with gaps'}
        {snapshot.coverage === 'unavailable' && 'Worker telemetry unavailable'}
      </strong>
      <ul>
        {snapshot.event_window_evictions > 0 && (
          <li>
            {snapshot.event_window_evictions.toLocaleString()} earlier events
            left the server window.
          </li>
        )}
        {snapshot.transport_dropped_lower_bound > 0 && (
          <li>
            At least {snapshot.transport_dropped_lower_bound.toLocaleString()}{' '}
            observations dropped in transport.
          </li>
        )}
        {snapshot.transport_loss_count_unquantified && (
          <li>Delivery loss could not be fully counted.</li>
        )}
        {snapshot.transport_unavailable && (
          <li>Worker observations were unavailable for an admitted pool.</li>
        )}
        <li>
          Receipt accounting determines image outcomes. Worker observations may
          be incomplete.
        </li>
      </ul>
    </aside>
  );
}

/** Render current snapshots and independently label retained worker evidence. */
export default function OperationDetails({
  snapshot,
  animateAfter,
  stale,
}: {
  readonly snapshot: OperationSnapshot;
  readonly animateAfter: number | null;
  readonly stale: boolean;
}) {
  return (
    <div className="monitor-details">
      {snapshot.cancel_requested && (
        <p className="monitor-notice">
          Cancellation requested.
          {!isOperationTerminal(snapshot.state) &&
            ' Dispatched tasks may still report outcomes.'}
        </p>
      )}
      {snapshot.error_code && (
        <p className="monitor-notice">
          Reported issue · {readableCode(snapshot.error_code)}
        </p>
      )}
      {snapshot.mode === 'adaptive' && <AdaptiveDetails snapshot={snapshot} />}
      <div className="monitor-columns">
        <FinalProgress progress={snapshot.progress} />
        <WorkerPool snapshot={snapshot} animateAfter={animateAfter} />
      </div>
      <TaskObservations
        snapshot={snapshot}
        animateAfter={animateAfter}
        stale={stale}
      />
      <Coverage snapshot={snapshot} />
      <RecentEvents snapshot={snapshot} />
    </div>
  );
}
