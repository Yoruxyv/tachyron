import { workers, type WorkerCount } from '@/api/contracts';
import type { OperationMode } from '@/api/operations';
import Select from '@/components/Select';

import type { OperationalState } from './useOperationMonitor';
import type { useOperationalExecution } from './useOperationalExecution';

export type ExecutionMode = 'benchmark' | OperationMode;

const modes = [
  { value: 'benchmark', label: 'Benchmark' },
  { value: 'manual', label: 'Manual' },
  { value: 'adaptive', label: 'Adaptive (experimental)' },
] as const;

const capacities = workers.map((count) => ({
  value: String(count),
  label: count === 1 ? '1 worker' : `${count} workers`,
}));

export function ModeField({
  id,
  mode,
  disabled,
  select,
}: {
  readonly id: string;
  readonly mode: ExecutionMode;
  readonly disabled: boolean;
  readonly select: (mode: ExecutionMode) => void;
}) {
  return (
    <div className="form-field">
      <label htmlFor={id}>Execution mode</label>
      <Select
        id={id}
        label="Execution mode"
        value={mode}
        options={modes}
        disabled={disabled}
        onChange={(value) => {
          const option = modes.find((entry) => entry.value === value);
          if (option) select(option.value);
        }}
      />
    </div>
  );
}

export function ManualWorkers({
  id,
  count,
  disabled,
  select,
}: {
  readonly id: string;
  readonly count: WorkerCount;
  readonly disabled: boolean;
  readonly select: (count: WorkerCount) => void;
}) {
  return (
    <div className="form-field">
      <label htmlFor={id}>Worker count</label>
      <Select
        id={id}
        label="Worker count"
        value={String(count)}
        options={capacities}
        disabled={disabled}
        onChange={(value) => {
          const worker = workers.find((entry) => String(entry) === value);
          if (worker !== undefined) select(worker);
        }}
      />
    </div>
  );
}

const lifecycleLabels: Record<OperationalState, string> = {
  QUEUED: 'Queued',
  RUNNING: 'Running',
  CANCELLING: 'Cancellation requested',
  COMPLETED: 'Completed',
  FAILED: 'Failed',
  CANCELLED: 'Cancelled',
  UNCONFIRMED: 'Outcome unconfirmed',
  UNAVAILABLE: 'Snapshot unavailable',
};

export function OperationStatus({
  execution,
}: {
  readonly execution: ReturnType<typeof useOperationalExecution>;
}) {
  const { sending, uncertain, error, runId, status, clearUncertainty } =
    execution;
  return (
    <section className="card stack" aria-labelledby="operation-status-heading">
      <div className="panel-heading">
        <div>
          <h2 id="operation-status-heading">Operational execution</h2>
          {runId && (
            <p>
              {status
                ? `Last verified · ${lifecycleLabels[status]}`
                : 'Run accepted · checking status'}
            </p>
          )}
        </div>
      </div>
      {sending && <output>Submitting operation…</output>}
      {uncertain && (
        <div className="notice">
          <p role="alert">
            The server may have accepted this operation. Check the local server
            before submitting again. If you find its run ID, attach it below.
          </p>
          <button type="button" onClick={clearUncertainty}>
            Allow a new submission
          </button>
        </div>
      )}
      {error && (
        <p className="notice" role="alert">
          {error}
        </p>
      )}
      {runId && (
        <p>
          <a
            href="#live-worker-monitor"
            onClick={(event) => {
              event.preventDefault();
              document.getElementById('live-worker-monitor')?.focus();
            }}
          >
            View run in Live Worker Monitor ↓
          </a>
        </p>
      )}
      {!sending && !uncertain && !error && !runId && (
        <p>Choose a dataset and start an operation to see live progress.</p>
      )}
    </section>
  );
}
