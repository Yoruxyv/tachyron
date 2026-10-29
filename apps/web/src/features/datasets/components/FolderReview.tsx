import { useEffect, useRef, useState } from 'react';
import type { useDatasetImport } from '../hooks/useDatasetImport';
import type { ReviewFile } from '../reviewFile';

type Importer = ReturnType<typeof useDatasetImport>;

/** Unsupported entries block the whole folder; only supported images may be excluded. */
export default function FolderReview({
  importer,
  disabled,
}: {
  readonly importer: Importer;
  readonly disabled: boolean;
}) {
  const { files, name } = importer;
  const unsupported = files.filter((entry) => entry.unsupported);
  const included = files.filter((entry) => entry.included);
  const bytes = included.reduce((total, entry) => total + entry.file.size, 0);
  const problems = included.filter((entry) => entry.problem);
  const excluded = files.length - included.length;
  const confirmed = useRef<HTMLInputElement>(null);
  const [acknowledgementError, setAcknowledgementError] = useState(false);
  useEffect(() => {
    if (confirmed.current) confirmed.current.checked = false;
    setAcknowledgementError(false);
  }, [files, name]);
  if (unsupported.length)
    return (
      <section aria-labelledby="unsupported-title" className="stack">
        <h3 id="unsupported-title">Unsupported files detected</h3>
        <p>
          Please include only PNG, JPG, or JPEG images in your dataset folder.
        </p>
        <p>
          {unsupported.length.toLocaleString()} unsupported{' '}
          {unsupported.length === 1 ? 'file' : 'files'} found:
        </p>
        <FilePages files={unsupported} />
        <p>
          Remove the unsupported files and select the folder again. No files
          were uploaded.
        </p>
      </section>
    );
  const overLimit =
    !importer.policy ||
    included.length > importer.policy.max_files ||
    bytes > importer.policy.max_total_bytes;
  return (
    <>
      <label>
        <span>Dataset name</span>
        <input
          maxLength={80}
          value={name}
          onChange={(event) => importer.setName(event.target.value)}
        />
      </label>
      <p>
        {included.length} included · {bytes.toLocaleString()} original bytes ·{' '}
        {excluded} explicitly excluded
      </p>
      <FilePages files={files} toggle={importer.toggle} />
      {problems.length > 0 && (
        <p className="request-error">
          {problems.length} included images exceed a file limit. Exclude them
          explicitly or choose another folder.
        </p>
      )}
      {overLimit && (
        <p className="request-error">
          Review the import policy and total file/byte limits before confirming.
        </p>
      )}
      <label className="checkbox-label import-confirmation">
        <input
          ref={confirmed}
          type="checkbox"
          aria-invalid={acknowledgementError || undefined}
          aria-describedby={
            acknowledgementError ? 'import-confirmation-error' : undefined
          }
          onChange={() => setAcknowledgementError(false)}
        />
        <span>
          I confirm {included.length} included files and {excluded} exclusions
          form this benchmark workload.
        </span>
      </label>
      {acknowledgementError && (
        <p id="import-confirmation-error" className="request-error">
          Acknowledge the included images and exclusions before importing.
        </p>
      )}
      <button
        className="primary"
        type="button"
        disabled={
          disabled || overLimit || !included.length || problems.length > 0
        }
        onClick={() => {
          if (confirmed.current?.checked) importer.start();
          else {
            if (!acknowledgementError) importer.requireConfirmation();
            setAcknowledgementError(true);
            confirmed.current?.focus();
          }
        }}
      >
        Confirm and import {included.length} files
      </button>
    </>
  );
}

function FilePages({
  files,
  toggle,
}: {
  readonly files: ReviewFile[];
  readonly toggle?: (index: number) => void;
}) {
  const [page, setPage] = useState(0);
  return (
    <>
      <section
        className="import-files"
        aria-label={
          toggle ? 'Selected folder files' : 'Unsupported folder files'
        }
      >
        <ul>
          {files.slice(page * 50, page * 50 + 50).map((entry, local) => (
            <li key={entry.path}>
              {toggle ? (
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={entry.included}
                    onChange={() => toggle(page * 50 + local)}
                  />
                  <FileDescription entry={entry} />
                </label>
              ) : (
                <FileDescription entry={entry} />
              )}
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
    </>
  );
}

function FileDescription({ entry }: { readonly entry: ReviewFile }) {
  return (
    <span>
      {entry.path}
      <small>
        {entry.file.size.toLocaleString()} bytes
        {entry.problem && ` · ${entry.problem}`}
      </small>
    </span>
  );
}
