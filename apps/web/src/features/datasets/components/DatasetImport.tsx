import { useEffect, useRef, useState } from 'react';
import type { useDatasetImport } from '../hooks/useDatasetImport';

/** Review an explicit workload; server validation remains authoritative. */
export default function DatasetImport({
  importer,
}: {
  readonly importer: ReturnType<typeof useDatasetImport>;
}) {
  const { files, operation } = importer;
  const [page, setPage] = useState(0);
  const status = useRef<HTMLOutputElement>(null);
  const confirmed = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (operation?.state === 'READY') status.current?.focus();
  }, [operation?.state]);
  useEffect(() => {
    if (confirmed.current) confirmed.current.checked = false;
  }, [files]);
  useEffect(() => {
    if (!importer.reviewing && files.length > 0) status.current?.focus();
  }, [files.length, importer.reviewing]);
  const included = files.filter((entry) => entry.included);
  const bytes = included.reduce((total, entry) => total + entry.file.size, 0);
  const problems = included.filter((entry) => entry.problem);
  const excluded = files.length - included.length;
  return (
    <section
      className="card dataset-import stack"
      aria-labelledby="import-title"
    >
      <div className="panel-heading">
        <div>
          <h2 id="import-title">Review dataset folder</h2>
          <p>Original PNG/JPEG files · private local storage</p>
        </div>
      </div>
      <p className="fine-print">
        Folder selection requires a current browser. Files and partial upload
        bytes cannot be restored after reload. Server validation checks image
        content and safety limits.
      </p>
      {files.length > 0 && (
        <>
          <label>
            <span>Dataset name</span>
            <input
              maxLength={80}
              value={importer.name}
              disabled={importer.busy}
              onChange={(event) => importer.setName(event.target.value)}
            />
          </label>
          <p>
            {included.length} included · {(bytes / 1048576).toFixed(2)} MiB ·{' '}
            {excluded} explicitly excluded
          </p>
          <section className="import-files" aria-label="Selected folder files">
            <ul>
              {files.slice(page * 50, page * 50 + 50).map((entry, local) => (
                <li key={entry.path}>
                  <label className="checkbox-label">
                    <input
                      type="checkbox"
                      checked={entry.included}
                      disabled={importer.busy}
                      onChange={() => importer.toggle(page * 50 + local)}
                    />
                    <span>
                      {entry.path}
                      <small>
                        {entry.file.size.toLocaleString()} bytes
                        {entry.problem && ` · ${entry.problem}`}
                      </small>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          </section>
          {files.length > 50 && (
            <div className="import-actions">
              <button
                type="button"
                disabled={page === 0}
                onClick={() => setPage((value) => value - 1)}
              >
                Previous files
              </button>
              <span>
                Page {page + 1} of {Math.ceil(files.length / 50)}
              </span>
              <button
                type="button"
                disabled={(page + 1) * 50 >= files.length}
                onClick={() => setPage((value) => value + 1)}
              >
                Next files
              </button>
            </div>
          )}
          {problems.length > 0 && (
            <p className="request-error">
              {problems.length} included files need review. Exclude them
              explicitly or choose another folder.
            </p>
          )}
          <label className="checkbox-label">
            <input ref={confirmed} type="checkbox" disabled={importer.busy} />I
            confirm {included.length} included files and {excluded} exclusions
            form this benchmark workload.
          </label>
          <button
            className="primary"
            type="button"
            disabled={
              importer.busy ||
              Boolean(importer.identifier) ||
              !importer.policy ||
              !included.length ||
              problems.length > 0
            }
            onClick={() => {
              if (confirmed.current?.checked) importer.start();
              else confirmed.current?.focus();
            }}
          >
            Confirm and import {included.length} files
          </button>
        </>
      )}
      <output ref={status} tabIndex={-1} aria-live="polite">
        {statusText(importer, included.length)}
        {operation?.cancel_requested &&
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
      {importer.error && (
        <p role="alert" className="request-error">
          {importer.error}
        </p>
      )}
      <div className="import-actions">
        {importer.busy && (
          <button
            type="button"
            onClick={() => {
              void importer.cancel();
            }}
          >
            Cancel import
          </button>
        )}
        <button type="button" onClick={importer.check}>
          Check import status
        </button>
      </div>
      {importer.identifier && (
        <p className="fine-print mono">Operation {importer.identifier}</p>
      )}
    </section>
  );
}

function statusText(
  importer: ReturnType<typeof useDatasetImport>,
  count: number,
): string {
  if (importer.reviewing) return 'Reading selected folder entries…';
  if (importer.preparing)
    return `Preparing originals: ${importer.prepared}/${count}`;
  const operation = importer.operation;
  if (operation?.state === 'READY')
    return 'Dataset registered and available for selection.';
  if (operation?.state === 'VALIDATING')
    return `Validating ${operation.progress.validated_files ?? 'unknown'}/${operation.progress.total_files ?? 'unknown'} files`;
  if (importer.uploading) return 'Transferring files; registration is pending.';
  if (operation) return `Import ${operation.state.toLowerCase()}`;
  if (importer.identifier) return 'Recovering recorded operation status…';
  if (count > 0)
    return `Review ${count} included files and confirm exclusions before uploading.`;
  return 'Choose a folder to review before uploading.';
}
