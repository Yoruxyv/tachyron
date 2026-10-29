import { useLayoutEffect, useRef } from 'react';
import { importTerminal } from '@/api/datasetImports';
import type { useDatasetImport } from '../hooks/useDatasetImport';
import FolderReview from './FolderReview';

/** One modal owns review and progress; closing it never abandons an operation. */
export default function DatasetImport({
  importer,
  disabled = false,
}: {
  readonly importer: ReturnType<typeof useDatasetImport>;
  readonly disabled?: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const title = useRef<HTMLHeadingElement>(null);
  useLayoutEffect(() => {
    const element = dialog.current;
    if (!element) return;
    const opener = document.activeElement;
    const root = document.documentElement;
    const { overflow, scrollbarGutter } = root.style;
    if (innerWidth > root.clientWidth) root.style.scrollbarGutter = 'stable';
    root.style.overflow = 'hidden';
    // Native bubbling also covers the diagnostics toast portaled into this dialog.
    const keydown = (event: KeyboardEvent) => containFocus(event, element);
    element.addEventListener('keydown', keydown);
    element.showModal();
    return () => {
      element.close();
      element.removeEventListener('keydown', keydown);
      root.style.overflow = overflow;
      root.style.scrollbarGutter = scrollbarGutter;
      if (
        opener instanceof HTMLElement &&
        opener.isConnected &&
        opener.matches('button, a, input, select, [tabindex]') &&
        !opener.matches(':disabled')
      )
        opener.focus({ preventScroll: true });
      else {
        const target =
          document.querySelector<HTMLButtonElement>(
            '.import-trigger:not(:disabled)',
          ) ?? document.getElementById('main-content');
        target?.focus({ preventScroll: true });
      }
    };
  }, []);
  useLayoutEffect(() => {
    title.current?.focus({ preventScroll: true });
  }, [importer.selectionRevision]);
  const { files, operation } = importer;
  const pending =
    importer.preparing || importer.uploading || Boolean(importer.identifier);
  const canCheck =
    !importer.policy ||
    Boolean(
      importer.identifier &&
      importer.error &&
      (!operation || !importTerminal(operation)),
    );
  return (
    <dialog
      ref={dialog}
      className="connection-dialog dataset-import"
      aria-modal="true"
      aria-labelledby="import-title"
      aria-describedby="import-guidance"
      onCancel={(event) => {
        event.preventDefault();
        importer.close();
      }}
    >
      <div className="connection-dialog-content stack">
        <h2 id="import-title" ref={title} tabIndex={-1}>
          Review dataset folder
        </h2>
        <p id="import-guidance" className="fine-print">
          Original PNG/JPEG files · private local storage. The server validates
          image content and safety limits. Files and partial upload bytes cannot
          be restored after reload.
        </p>
        {!pending && !importer.reviewing && files.length > 0 && (
          <FolderReview
            key={importer.selectionRevision}
            importer={importer}
            disabled={disabled}
          />
        )}
        <output aria-live="polite" aria-atomic="true">
          {statusText(importer)}
          {(importer.cancelling || operation?.cancel_requested) &&
            ' Cancellation requested; waiting for confirmation.'}
        </output>
        {importer.uploading && (
          <label>
            Upload transfer
            <progress max={1} value={importer.progress ?? undefined} />
            {importer.progress === null
              ? 'Waiting for transfer progress'
              : `${Math.round(importer.progress * 100)}% transferred`}
          </label>
        )}
        {importer.error && <p className="request-error">{importer.error}</p>}
        {importer.busy && (
          <p className="fine-print">
            Keep this tab open during transfer. Use Cancel import to stop;
            closing this dialog is disabled until the outcome is confirmed.
          </p>
        )}
        <div className="import-actions">
          {importer.busy ? (
            <button
              type="button"
              disabled={importer.cancelling || operation?.cancel_requested}
              onClick={() => {
                void importer.cancel();
              }}
            >
              {importer.cancelling ? 'Cancelling…' : 'Cancel import'}
            </button>
          ) : (
            <>
              <button type="button" data-import-close onClick={importer.close}>
                Close
              </button>
              <button
                type="button"
                disabled={disabled}
                onClick={() =>
                  document.getElementById('import-folder')?.click()
                }
              >
                Choose another folder
              </button>
            </>
          )}
          {canCheck && (
            <button type="button" onClick={importer.check}>
              {importer.identifier
                ? 'Check import status'
                : 'Retry import policy'}
            </button>
          )}
        </div>
        {importer.identifier && (
          <p className="fine-print mono">Operation {importer.identifier}</p>
        )}
        <div id="import-notifications" />
      </div>
    </dialog>
  );
}

function containFocus(event: KeyboardEvent, dialog: HTMLDialogElement) {
  if (event.key !== 'Tab') return;
  const controls = dialog.querySelectorAll<HTMLElement>(
    'button:not(:disabled), input:not(:disabled), [tabindex="0"]',
  );
  const first = controls[0];
  const last = controls[controls.length - 1];
  if (
    event.shiftKey &&
    (first?.matches(':focus') || document.activeElement?.tagName === 'H2')
  ) {
    event.preventDefault();
    last?.focus();
  } else if (!event.shiftKey && last?.matches(':focus')) {
    event.preventDefault();
    first?.focus();
  }
}

function statusText(importer: ReturnType<typeof useDatasetImport>): string {
  const included = importer.files.filter((entry) => entry.included).length;
  if (importer.reviewing) return 'Inspecting selected folder entries…';
  if (importer.preparing)
    return `Preparing originals: ${importer.prepared}/${included}`;
  const operation = importer.operation;
  if (operation?.state === 'VALIDATING')
    return `Validating ${operation.progress.validated_files ?? 'unknown'}/${operation.progress.total_files ?? 'unknown'} ${operation.progress.total_files === 1 ? 'file' : 'files'}`;
  if (importer.uploading) return 'Transferring files; registration is pending.';
  if (operation) return `Import ${operation.state.toLowerCase()}`;
  if (importer.identifier) return 'Recovering recorded operation status…';
  if (
    importer.files.length &&
    !importer.files.some((entry) => entry.unsupported)
  )
    return `Review ${included} included ${included === 1 ? 'file' : 'files'} and confirm exclusions before uploading.`;
  return '';
}
