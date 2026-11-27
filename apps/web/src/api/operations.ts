import { request } from './benchmarks';
import { workers, type WorkerCount } from './contracts';
import {
  array,
  boolean,
  integer,
  invalid,
  nullable,
  object,
  string,
  timestamp,
} from './decode';

export const eventWindow = 256;
const phases = ['queued', 'preparing', 'calibration', 'execution'] as const;
const terminalStates = [
  'COMPLETED',
  'FAILED',
  'CANCELLED',
  'UNCONFIRMED',
] as const;
const poolStates = ['RUNNING', ...terminalStates] as const;
const runStates = ['QUEUED', ...poolStates] as const;

function choice<T>(value: unknown, choices: readonly T[]): T {
  const match = choices.find((option) => option === value);
  if (match === undefined) throw invalid();
  return match;
}

export function isRunId(value: string): boolean {
  return /^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/iu.test(value);
}

function id(value: unknown) {
  const result = string(value);
  if (!isRunId(result)) throw invalid();
  return result;
}

function instance(value: unknown) {
  const result = string(value);
  if (!/^[\da-f]{32}$/u.test(result)) throw invalid();
  return result;
}

function exitCode(value: unknown) {
  if (typeof value !== 'number' || !Number.isSafeInteger(value))
    throw invalid();
  return value;
}

function count(value: unknown) {
  const result = integer(value);
  if (!Number.isSafeInteger(result)) throw invalid();
  return result;
}

function positive(value: unknown) {
  const result = count(value);
  if (result === 0) throw invalid();
  return result;
}

function capacity(value: unknown) {
  return choice(value, workers);
}

function progress(value: unknown) {
  const data = object(value);
  const total = count(data.total);
  const outcomes = {
    pending: count(data.pending),
    succeeded: count(data.succeeded),
    failed: count(data.failed),
    unknown: count(data.unknown),
    not_started: count(data.not_started),
  };
  if (Object.values(outcomes).reduce((sum, item) => sum + item, 0) !== total)
    throw invalid();
  return { total, ...outcomes };
}

function worker(value: unknown) {
  const data = object(value);
  const state = choice(data.state, [
    'OBSERVED',
    'STARTED',
    'READY',
    'EXITED',
  ] as const);
  return {
    instance_id: instance(data.instance_id),
    pid: positive(data.pid),
    state,
    exit_code: state === 'EXITED' ? exitCode(data.exit_code) : null,
  };
}

function trialScope(value: unknown) {
  const data = object(value);
  return { index: count(data.index), repetition: count(data.repetition) };
}

function pool(value: unknown) {
  const data = object(value);
  const requested = capacity(data.requested_workers);
  const observed = data.observed_workers;
  if (!Array.isArray(observed) || observed.length > requested) throw invalid();
  const instances = array(observed, worker);
  if (
    new Set(instances.map((entry) => entry.instance_id)).size !==
    instances.length
  )
    throw invalid();
  return {
    execution_id: id(data.execution_id),
    requested_workers: requested,
    trial: nullable(data.trial, trialScope),
    state: choice(data.state, poolStates),
    progress: progress(data.progress),
    observed_workers: instances,
  };
}

function selection(value: unknown) {
  const data = object(value);
  const status = choice(data.status, ['SELECTED', 'INCONCLUSIVE'] as const);
  const fallback = boolean(data.fallback);
  if (fallback !== (status === 'INCONCLUSIVE')) throw invalid();
  return {
    fallback,
    workers: capacity(data.workers),
    reason: string(data.reason),
    profiled_inputs: count(data.profiled_inputs),
  };
}

function optional<T>(value: unknown, parse: (input: unknown) => T): T | null {
  return value === undefined ? null : nullable(value, parse);
}

function event(value: unknown) {
  const data = object(value);
  if (data.version !== 1) throw invalid();
  return {
    sequence: positive(data.sequence),
    phase: choice(data.phase, phases),
    type: string(data.type),
    origin: choice(data.origin, ['parent', 'child'] as const),
    execution_id: optional(data.execution_id, id),
    trial_index: optional(data.trial_index, count),
    instance_id: optional(data.instance_id, instance),
    pid: optional(data.pid, positive),
    index: optional(data.index, count),
    exit_code: optional(data.exit_code, exitCode),
  };
}

function eventWindowFrom(data: Record<string, unknown>, after: number | null) {
  const last = positive(data.snapshot_sequence);
  const first = positive(data.first_available_sequence);
  const evictions = count(data.event_window_evictions);
  const resync = boolean(data.resync_required);
  const staleCursor = after !== null && (after < first - 1 || after > last);
  const start = after === null || resync ? first : after + 1;
  if (
    first > last ||
    last - first >= eventWindow ||
    evictions !== first - 1 ||
    resync !== staleCursor ||
    !Array.isArray(data.events) ||
    data.events.length !== last - start + 1
  ) {
    throw invalid();
  }
  const events = array(data.events, event);
  if (events.some((entry, index) => entry.sequence !== start + index))
    throw invalid();
  return {
    snapshot_sequence: last,
    first_available_sequence: first,
    event_window_evictions: evictions,
    resync_required: resync,
    events,
  };
}

export type OperationMode = 'manual' | 'adaptive';

interface OperationInput {
  readonly dataset_id: string;
  readonly width: number;
  readonly height: number;
}

export type OperationRequest = OperationInput &
  (
    | { readonly mode: 'manual'; readonly workers: WorkerCount }
    | { readonly mode: 'adaptive' }
  );

/** Decode the consumed C2 fields and verify the server's window against this request. */
export function parseOperation(
  value: unknown,
  runId: string,
  after: number | null,
) {
  const data = object(value);
  if (data.schema_version !== 1 || data.run_id !== runId) throw invalid();
  return {
    run_id: id(data.run_id),
    dataset_id: nullable(data.dataset_id, string),
    mode: choice(data.mode, ['manual', 'adaptive'] as const),
    requested_workers: nullable(data.requested_workers, capacity),
    state: choice(data.state, runStates),
    phase: choice(data.phase, phases),
    cancel_requested: boolean(data.cancel_requested),
    error_code: nullable(data.error_code, string),
    progress: progress(data.progress),
    pool: nullable(data.pool, pool),
    selection: nullable(data.selection, selection),
    calibration_trials_finished: count(data.calibration_trials_finished),
    final_execution_id: nullable(data.final_execution_id, id),
    observed_at: timestamp(data.observed_at),
    transport_dropped_lower_bound: count(data.transport_dropped_lower_bound),
    transport_loss_count_unquantified: boolean(
      data.transport_loss_count_unquantified,
    ),
    transport_unavailable: boolean(data.transport_unavailable),
    coverage: choice(data.coverage, [
      'best_effort',
      'best_effort_with_gaps',
      'unavailable',
    ] as const),
    ...eventWindowFrom(data, after),
  };
}

export type OperationSnapshot = ReturnType<typeof parseOperation>;
export type OperationState = OperationSnapshot['state'];
export type OperationEvent = ReturnType<typeof event>;
export type OperationProgress = ReturnType<typeof progress>;

export const isOperationTerminal = (state: OperationSnapshot['state']) =>
  state !== 'QUEUED' && state !== 'RUNNING';

/** Admit one operation. A missing acceptance response leaves admission uncertain. */
export function startOperation(body: OperationRequest, signal: AbortSignal) {
  return request(
    '/api/operations',
    (value) => {
      const snapshot = parseOperation(value, id(object(value).run_id), null);
      if (
        snapshot.dataset_id !== body.dataset_id ||
        snapshot.mode !== body.mode
      )
        throw invalid();
      if (body.mode === 'manual' && snapshot.requested_workers !== body.workers)
        throw invalid();
      return snapshot;
    },
    signal,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  );
}

export function cancelOperation(runId: string, signal: AbortSignal) {
  return request(
    `/api/operations/${encodeURIComponent(runId)}/cancel`,
    (body) => {
      const snapshot = parseOperation(body, runId, null);
      if (!snapshot.cancel_requested && !isOperationTerminal(snapshot.state))
        throw invalid();
      return snapshot;
    },
    signal,
    { method: 'POST' },
  );
}

export function getOperation(
  runId: string,
  after: number | null,
  signal: AbortSignal,
) {
  const query = after === null ? '' : `?after=${after}`;
  return request(
    `/api/operations/${encodeURIComponent(runId)}${query}`,
    (body) => parseOperation(body, runId, after),
    signal,
    { cache: 'no-store' },
  );
}
