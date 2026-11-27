import { useRef, useState, type SubmitEvent } from 'react';

import { isRunId } from '@/api/operations';
import Icon from '@/components/Icon';

import OperationDetails, { OperationPhase } from './OperationDetails';
import { useOperationMonitor } from './useOperationMonitor';
import './live-worker-monitor.css';

const connectionLabels = {
  connecting: 'Connecting',
  live: 'Live',
  stale: 'Reconnecting',
  unavailable: 'Unavailable',
  terminal: 'Terminal snapshot',
};

const runLabels = {
  QUEUED: 'Queued',
  RUNNING: 'Running',
  COMPLETED: 'Completed',
  FAILED: 'Failed',
  CANCELLED: 'Cancelled',
  UNCONFIRMED: 'Outcome unconfirmed',
};

function AttachedRun({ runId }: { readonly runId: string }) {
  const { snapshot, connection, message, animateAfter, retry } =
    useOperationMonitor(runId);
  return (
    <>
      <div className="monitor-status">
        <output className="monitor-connection" data-connection={connection}>
          {connectionLabels[connection]}
          {snapshot && ` · ${runLabels[snapshot.state]}`}
        </output>
        {snapshot && (
          <p>
            {snapshot.mode === 'adaptive' ? 'Adaptive' : 'Manual'} ·{' '}
            <OperationPhase phase={snapshot.phase} />
            {snapshot.dataset_id && ` · ${snapshot.dataset_id}`}
          </p>
        )}
        {connection === 'unavailable' && (
          <button type="button" onClick={retry}>
            Retry connection
          </button>
        )}
      </div>
      {message && <output className="monitor-notice">{message}</output>}
      {snapshot && (
        <OperationDetails snapshot={snapshot} animateAfter={animateAfter} />
      )}
    </>
  );
}

/** Attach to an existing run without changing operational or benchmark state. */
export default function LiveWorkerMonitor() {
  const input = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState('');
  const [runId, setRunId] = useState<string | null>(null);
  const [invalid, setInvalid] = useState(false);
  const normalized = draft.trim().toLowerCase();

  function attach(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!isRunId(normalized)) {
      setInvalid(true);
      return;
    }
    setInvalid(false);
    setRunId(normalized);
  }

  return (
    <section
      className="card live-worker-monitor"
      aria-labelledby="live-worker-monitor-heading"
    >
      <header className="monitor-heading">
        <div className="monitor-heading-icon">
          <Icon name="cpu" />
        </div>
        <div>
          <h2 id="live-worker-monitor-heading">Live Worker Monitor</h2>
          <p>Observe an existing operational run by its run ID.</p>
        </div>
      </header>
      <form className="monitor-attach" onSubmit={attach} noValidate>
        <label htmlFor="monitor-run-id">Operational run ID</label>
        <div className="monitor-attach-actions">
          <input
            id="monitor-run-id"
            ref={input}
            type="text"
            autoCapitalize="none"
            value={draft}
            autoComplete="off"
            spellCheck={false}
            aria-invalid={invalid}
            aria-describedby={invalid ? 'monitor-invalid-id' : undefined}
            onChange={(event) => {
              setDraft(event.target.value);
              setInvalid(false);
            }}
          />
          <button
            type="submit"
            disabled={runId !== null && runId === normalized}
          >
            Attach
          </button>
          {runId && (
            <button
              type="button"
              onClick={() => {
                setRunId(null);
                input.current?.focus();
              }}
            >
              Detach
            </button>
          )}
        </div>
        {invalid && (
          <p className="monitor-notice" role="alert" id="monitor-invalid-id">
            Enter a valid run ID in UUID format.
          </p>
        )}
      </form>
      {runId && (
        <div className="monitor-attached">
          <p className="monitor-run-id">
            <span>Monitoring</span> <code>{runId}</code>
          </p>
          <AttachedRun key={runId} runId={runId} />
        </div>
      )}
    </section>
  );
}
