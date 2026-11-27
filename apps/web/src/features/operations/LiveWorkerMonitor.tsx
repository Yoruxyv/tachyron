import { useRef, useState, type SubmitEvent } from 'react';

import { isRunId } from '@/api/operations';
import Icon from '@/components/Icon';

import OperationCancellation from './OperationCancellation';
import OperationDetails, { OperationPhase } from './OperationDetails';
import {
  useOperationMonitor,
  type OperationalState,
} from './useOperationMonitor';
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

function AttachedRun({
  runId,
  onState,
  owned,
}: {
  readonly runId: string;
  readonly onState: ((id: string, state: OperationalState) => void) | undefined;
  readonly owned: boolean;
}) {
  const { snapshot, connection, message, animateAfter, retry } =
    useOperationMonitor(runId, onState);
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
      {owned && snapshot && (
        <OperationCancellation
          snapshot={snapshot}
          live={connection === 'live'}
        />
      )}
      {message && <output className="monitor-notice">{message}</output>}
      {snapshot && (
        <OperationDetails snapshot={snapshot} animateAfter={animateAfter} />
      )}
    </>
  );
}

function RunIdentity({ runId }: { readonly runId: string }) {
  const [feedback, setFeedback] = useState('');

  async function copy() {
    try {
      await navigator.clipboard.writeText(runId);
      setFeedback('Run ID copied.');
    } catch {
      setFeedback('Clipboard unavailable. Select the run ID to copy it.');
    }
  }

  return (
    <div className="monitor-run-id">
      <span>Monitoring</span> <code>{runId}</code>
      <button type="button" onClick={() => void copy()}>
        Copy run ID
      </button>
      <output>{feedback}</output>
    </div>
  );
}

interface Props {
  readonly acceptedRunId?: string | null;
  readonly locked?: boolean;
  readonly onState?: (id: string, state: OperationalState) => void;
}

/** Follow an accepted run or attach to an existing one for observation. */
export default function LiveWorkerMonitor({
  acceptedRunId = null,
  locked = false,
  onState,
}: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState('');
  const [runId, setRunId] = useState<string | null>(acceptedRunId);
  const [invalid, setInvalid] = useState(false);
  const normalized = draft.trim().toLowerCase();

  function attach(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (locked) return;
    if (!isRunId(normalized)) {
      setInvalid(true);
      return;
    }
    setInvalid(false);
    setRunId(normalized);
  }

  return (
    <section
      id="live-worker-monitor"
      tabIndex={-1}
      className="card live-worker-monitor"
      aria-labelledby="live-worker-monitor-heading"
    >
      <header className="monitor-heading">
        <div className="monitor-heading-icon">
          <Icon name="cpu" />
        </div>
        <div>
          <h2 id="live-worker-monitor-heading">Live Worker Monitor</h2>
          <p>Runs started here attach automatically.</p>
        </div>
      </header>
      {!locked && (
        <details className="monitor-attach-options">
          <summary>Attach existing run</summary>
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
              <p
                className="monitor-notice"
                role="alert"
                id="monitor-invalid-id"
              >
                Enter a valid run ID in UUID format.
              </p>
            )}
          </form>
        </details>
      )}
      {runId && (
        <div className="monitor-attached">
          <RunIdentity key={`identity-${runId}`} runId={runId} />
          <AttachedRun
            key={runId}
            runId={runId}
            owned={runId === acceptedRunId}
            onState={runId === acceptedRunId ? onState : undefined}
          />
        </div>
      )}
    </section>
  );
}
