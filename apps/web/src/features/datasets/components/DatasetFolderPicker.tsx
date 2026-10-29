import { useEffect, useId, useRef } from 'react';

/** Open the native directory picker directly from an accessible button. */
export default function DatasetFolderPicker({
  disabled,
  choose,
}: {
  readonly disabled: boolean;
  readonly choose: (files: File[]) => Promise<void>;
}) {
  const input = useRef<HTMLInputElement>(null);
  const guidanceId = useId();
  useEffect(() => {
    input.current?.setAttribute('webkitdirectory', '');
  }, []);

  return (
    <div className="import-picker-row">
      <button
        type="button"
        className="import-trigger"
        aria-describedby={guidanceId}
        disabled={disabled}
        onClick={() => input.current?.click()}
      >
        Import folder
      </button>
      <span id={guidanceId} className="fine-print import-guidance">
        PNG, JPG, JPEG only. Files must be valid and uncorrupted.
      </span>
      <input
        id="import-folder"
        ref={input}
        type="file"
        aria-label="Choose image folder"
        multiple
        hidden
        disabled={disabled}
        onChange={(event) => {
          const files = Array.from(event.currentTarget.files ?? []);
          // Reset after capturing File objects so selecting the same folder fires change.
          event.currentTarget.value = '';
          void choose(files);
        }}
      />
    </div>
  );
}
