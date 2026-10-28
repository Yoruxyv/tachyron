import {
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
} from 'react';

import type { useApiHealth } from './useApiHealth';

const startCommand =
  'uv run --locked --project apps/server uvicorn tachyron.api:app --host 127.0.0.1 --port 8000';

export default function ConnectionTroubleshooting({
  health,
  connection,
  onClose,
  restoreFocus,
}: {
  readonly health: ReturnType<typeof useApiHealth>;
  readonly connection: string;
  readonly onClose: () => void;
  readonly restoreFocus: () => void;
}) {
  const titleId = useId();
  const descriptionId = useId();
  const dialog = useRef<HTMLDialogElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const copying = useRef(false);
  const [copyState, setCopyState] = useState<
    'idle' | 'copying' | 'copied' | 'failed'
  >('idle');

  useLayoutEffect(() => {
    const element = dialog.current;
    if (!element) return;
    const root = document.documentElement;
    const { overflow, scrollbarGutter } = root.style;
    // Retain an existing scrollbar gutter without creating one on short pages.
    if (innerWidth > root.clientWidth) root.style.scrollbarGutter = 'stable';
    root.style.overflow = 'hidden';
    element.closedBy = 'any';
    element.showModal();
    closeButton.current?.focus({ preventScroll: true });
    return () => {
      element.close();
      root.style.overflow = overflow;
      root.style.scrollbarGutter = scrollbarGutter;
      restoreFocus();
    };
  }, [restoreFocus]);

  async function copyCommand() {
    if (copying.current) return;
    copying.current = true;
    setCopyState('copying');
    try {
      await navigator.clipboard.writeText(startCommand);
      if (dialog.current?.open) setCopyState('copied');
    } catch {
      if (dialog.current?.open) setCopyState('failed');
    } finally {
      copying.current = false;
    }
  }

  function containFocus(event: KeyboardEvent<HTMLDialogElement>) {
    if (event.key !== 'Tab') return;
    const buttons = event.currentTarget.querySelectorAll<HTMLButtonElement>(
      'button:not(:disabled)',
    );
    const first = buttons[0];
    const last = buttons[buttons.length - 1];
    if (event.shiftKey && first?.matches(':focus')) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && last?.matches(':focus')) {
      event.preventDefault();
      first?.focus();
    }
  }

  return (
    <dialog
      ref={dialog}
      className="connection-dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClose={(event) => {
        if (!event.currentTarget.open) onClose();
      }}
      onKeyDown={containFocus}
    >
      <div className="connection-dialog-content">
        <button
          ref={closeButton}
          type="button"
          className="dialog-close-icon"
          aria-label="Close troubleshooting"
          onClick={onClose}
        >
          <svg
            viewBox="0 0 20 20"
            width="16"
            height="16"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            aria-hidden="true"
          >
            <path d="m5 5 10 10M15 5 5 15" />
          </svg>
        </button>
        <h2 id={titleId}>Connection troubleshooting</h2>
        <div className="dialog-connection">
          <span
            className="workspace-dot"
            data-live={health.status === 'API online' && !health.checking}
            aria-hidden="true"
          />
          <output aria-live="polite" aria-atomic="true">
            {connection}
          </output>
        </div>
        <p id={descriptionId}>
          The local FastAPI backend must be running. Start it from the
          repository root in PowerShell.
        </p>
        <div className="connection-command">
          <div>
            <span>PowerShell</span>
            <button
              type="button"
              onClick={copyCommand}
              aria-disabled={copyState === 'copying'}
              aria-busy={copyState === 'copying'}
            >
              {copyState === 'copying' ? 'Copying…' : 'Copy'}
            </button>
          </div>
          <pre>
            <code>{startCommand}</code>
          </pre>
        </div>
        <output className="copy-feedback" aria-live="polite" aria-atomic="true">
          {copyState === 'copied' && 'Command copied.'}
          {copyState === 'failed' &&
            'Could not copy. Select the command and copy it manually.'}
        </output>
        <p className="dialog-guidance">
          Vite normally runs on port 5173 and proxies <code>/api</code> and{' '}
          <code>/health</code> to port 8000. For another backend address, set{' '}
          <code>TACHYRON_API_URL</code> before starting Vite.
        </p>
        <p className="dialog-guidance">
          Connectivity does not establish readiness. Datasets and job status are
          verified separately.
        </p>
        <div className="dialog-actions">
          <button type="button" onClick={onClose}>
            Close
          </button>
          <button
            type="button"
            className="primary"
            onClick={health.check}
            aria-disabled={health.checking}
            aria-busy={health.checking}
          >
            {health.checking ? 'Checking…' : 'Retry connection'}
          </button>
        </div>
      </div>
    </dialog>
  );
}
