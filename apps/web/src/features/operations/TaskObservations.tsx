import type { OperationEvent, OperationSnapshot } from '@/api/operations';

const maxTaskRows = 4;

const columns = {
  dispatch: 'Dispatch',
  start: 'Worker start',
  outcome: 'Outcome',
} as const;

type Stage = keyof typeof columns;
type Tone =
  'dispatch' | 'start' | 'success' | 'failure' | 'uncertain' | 'neutral';

interface Signal {
  stage: Stage;
  label: string;
  tone: Tone;
}

const signals: Readonly<Partial<Record<string, Signal>>> = {
  task_submitted: { stage: 'dispatch', label: 'Submitted', tone: 'dispatch' },
  submission_unknown: {
    stage: 'dispatch',
    label: 'Submission unconfirmed',
    tone: 'uncertain',
  },
  task_started: { stage: 'start', label: 'Start observed', tone: 'start' },
  task_result_received: {
    stage: 'outcome',
    label: 'Result received · acknowledgement unobserved',
    tone: 'dispatch',
  },
  task_succeeded: {
    stage: 'outcome',
    label: 'Success acknowledged',
    tone: 'success',
  },
  task_failed: {
    stage: 'outcome',
    label: 'Failure acknowledged',
    tone: 'failure',
  },
  task_unknown: {
    stage: 'outcome',
    label: 'Outcome unknown',
    tone: 'uncertain',
  },
  task_not_started: { stage: 'outcome', label: 'Not started', tone: 'neutral' },
};

interface Observation extends Signal {
  sequence: number;
  pid: number | null;
}

type TaskRow = Partial<Record<Stage, Observation>>;

function taskSignal(event: OperationEvent) {
  const signal = Object.hasOwn(signals, event.type)
    ? signals[event.type]
    : undefined;
  if (
    signal &&
    event.origin === (signal.stage === 'start' ? 'child' : 'parent')
  )
    return signal;
  return undefined;
}

function recentTaskRows(events: readonly OperationEvent[], poolId: string) {
  const rows = new Map<number, TaskRow>();
  // Keep the latest observation per stage for the most recently observed tasks.
  for (const event of events.toReversed()) {
    if (event.execution_id !== poolId || event.index === null) continue;
    const signal = taskSignal(event);
    if (!signal) continue;
    const row = rows.get(event.index) ?? {};
    if (!rows.has(event.index) && rows.size === maxTaskRows) continue;
    row[signal.stage] ??= {
      ...signal,
      sequence: event.sequence,
      pid: event.pid,
    };
    rows.set(event.index, row);
  }
  return rows;
}

function TaskStage({
  stage,
  observation,
  animateAfter,
}: {
  readonly stage: Stage;
  readonly observation: Observation | undefined;
  readonly animateAfter: number | null;
}) {
  return (
    <div className="monitor-task-stage">
      <dt>{columns[stage]}</dt>
      <dd data-tone={observation?.tone ?? 'missing'}>
        {observation &&
          animateAfter !== null &&
          observation.sequence > animateAfter && (
            <span
              key={observation.sequence}
              className="monitor-task-pulse"
              aria-hidden="true"
            />
          )}
        <span>{observation?.label ?? 'No observation'}</span>
        {stage === 'start' && observation?.pid != null && (
          <small>PID {observation.pid}</small>
        )}
      </dd>
    </div>
  );
}

/** Show retained task evidence for one pool without inferring missing stages. */
export default function TaskObservations({
  snapshot,
  animateAfter,
  stale,
}: {
  readonly snapshot: OperationSnapshot;
  readonly animateAfter: number | null;
  readonly stale: boolean;
}) {
  const { pool, events } = snapshot;
  const rows = pool ? [...recentTaskRows(events, pool.execution_id)] : [];
  return (
    <section
      className="monitor-panel monitor-flow"
      aria-labelledby="monitor-flow-heading"
    >
      <div className="monitor-panel-heading">
        <h3 id="monitor-flow-heading">Execution flow</h3>
        {pool && (
          <span className="monitor-flow-scope">
            {pool.trial
              ? `Calibration trial #${pool.trial.index + 1}`
              : 'Final processing pool'}
          </span>
        )}
      </div>
      <p className="monitor-note">
        {stale
          ? 'Last verified task observations.'
          : 'Recent task observations for this pool.'}{' '}
        Missing observations leave a stage unconfirmed.
      </p>
      {pool && rows.length > 0 ? (
        <ol
          className="monitor-task-list"
          aria-label="Recent tasks in this pool"
        >
          {rows.map(([index, row]) => (
            <li key={`${pool.execution_id}:${index}`}>
              <h4>Task #{index}</h4>
              <dl>
                <TaskStage
                  stage="dispatch"
                  observation={row.dispatch}
                  animateAfter={animateAfter}
                />
                <TaskStage
                  stage="start"
                  observation={row.start}
                  animateAfter={animateAfter}
                />
                <TaskStage
                  stage="outcome"
                  observation={row.outcome}
                  animateAfter={animateAfter}
                />
              </dl>
            </li>
          ))}
        </ol>
      ) : (
        <p className="monitor-flow-empty">
          {pool
            ? 'No task observations available for this pool.'
            : 'Task observations appear after a pool is admitted.'}
        </p>
      )}
    </section>
  );
}
