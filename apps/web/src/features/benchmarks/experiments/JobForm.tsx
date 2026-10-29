import { useId, useState, type ReactNode, type SubmitEvent } from 'react';

import {
  type Dataset,
  type RunRequest,
  type WorkerCount,
} from '@/api/contracts';
import Select from '@/components/Select';

import DatasetSelect from '@/features/datasets/components/DatasetSelect';
import WorkerSelector from '../results/WorkerSelector';

interface Props {
  readonly importAction?: ReactNode;
  readonly datasets: Dataset[];
  readonly disabled: boolean;
  readonly pending: RunRequest | undefined;
  readonly submit: (request: RunRequest) => Promise<void>;
  readonly datasetId: string;
  readonly selectDataset: (id: string) => void;
  readonly compact?: boolean;
  readonly selectedWorker?: WorkerCount;
  readonly selectWorker?: (count: WorkerCount) => void;
  readonly datasetError?: string;
  readonly retryDatasets?: () => void;
}
export default function JobForm({
  datasets,
  disabled,
  pending,
  submit,
  datasetId,
  selectDataset,
  compact = false,
  selectedWorker = 4,
  selectWorker,
  datasetError = '',
  retryDatasets,
  importAction,
}: Props) {
  const id = useId();
  const [width, setWidth] = useState('256');
  const [height, setHeight] = useState('256');
  const [repetitions, setRepetitions] = useState('5');
  const [tiny, setTiny] = useState(false);
  const [error, setError] = useState('');
  const [settingsOpen, setSettingsOpen] = useState(!compact);
  const selected = pending?.dataset_id ?? datasetId;
  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (disabled) return;
    const request: RunRequest = pending ?? {
      dataset_id: selected,
      operation: 'resize-grayscale-v1',
      width: Number(width),
      height: Number(height),
      repetitions: Number(repetitions),
      seed: 2026,
      sampling_interval: 0.1,
      tiny,
    };
    const dataset = datasets.find(
      (item) => item.dataset_id === request.dataset_id,
    );
    const validDimension = (value: number) =>
      Number.isInteger(value) && value >= 1 && value <= 4096;
    if (
      !pending &&
      (dataset?.error_code !== null ||
        !dataset.input_count ||
        !validDimension(request.width) ||
        !validDimension(request.height) ||
        !Number.isInteger(request.repetitions) ||
        request.repetitions < (tiny ? 1 : 5) ||
        request.repetitions > 20)
    ) {
      setError(
        'Choose an available dataset, dimensions from 1 to 4096, and 5–20 measured repetitions (1–20 for a tiny functional test).',
      );
      setSettingsOpen(true);
      return;
    }
    setError('');
    submit(request).catch(() =>
      setError('Unable to submit. Retry with the same configuration.'),
    );
  }
  return (
    <section
      className={`card quick-card${compact ? ' compact' : ''}`}
      aria-labelledby={`${id}-title`}
    >
      <div className="panel-heading">
        <div>
          <h2 id={`${id}-title`}>
            {compact ? 'Quick experiment' : 'Experiment Configuration'}
          </h2>
          <p>Configure a measured benchmark run</p>
        </div>
        <svg
          viewBox="0 0 24 24"
          width="20"
          height="20"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          aria-hidden="true"
        >
          <path d="M8 3h8m-6 0v7L5 19a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-9V3M8 16h8" />
        </svg>
      </div>
      <form onSubmit={handleSubmit} noValidate>
        <fieldset disabled={disabled || Boolean(pending)}>
          <div className="form-field">
            <label htmlFor={`${id}-operation`}>Processing operation</label>
            <Select
              id={`${id}-operation`}
              label="Processing operation"
              value="resize-grayscale-v1"
              onChange={() => undefined}
              disabled={disabled || Boolean(pending)}
              options={[
                { value: 'resize-grayscale-v1', label: 'Resize + Grayscale' },
              ]}
            />
          </div>
          <div className={compact ? 'form-columns' : 'form-field'}>
            <div className="form-field">
              <label htmlFor={`${id}-dataset`}>Dataset</label>
              {importAction}
              <DatasetSelect
                id={`${id}-dataset`}
                datasets={datasets}
                value={selected}
                onChange={selectDataset}
                disabled={disabled || Boolean(pending)}
              />
            </div>
            {compact && selectWorker && (
              <WorkerSelector value={selectedWorker} onChange={selectWorker} />
            )}
          </div>
          <details
            className="benchmark-settings"
            open={settingsOpen}
            onToggle={(event) => setSettingsOpen(event.currentTarget.open)}
          >
            <summary>
              Benchmark settings{' '}
              <span className="mono">
                {pending?.width ?? width} × {pending?.height ?? height} ·{' '}
                {pending?.repetitions ?? repetitions} runs
                {(pending?.tiny ?? tiny) && ' · tiny test'}
              </span>
            </summary>
            <div className="settings-content">
              <div className="form-columns">
                <label>
                  <span>Width (px)</span>
                  <input
                    type="number"
                    min="1"
                    max="4096"
                    required
                    value={pending?.width ?? width}
                    onChange={(event) => setWidth(event.target.value)}
                  />
                </label>
                <label>
                  <span>Height (px)</span>
                  <input
                    type="number"
                    min="1"
                    max="4096"
                    required
                    value={pending?.height ?? height}
                    onChange={(event) => setHeight(event.target.value)}
                  />
                </label>
              </div>
              <label>
                <span>Measured repetitions per worker</span>
                <input
                  type="number"
                  min={tiny ? 1 : 5}
                  max="20"
                  required
                  value={pending?.repetitions ?? repetitions}
                  onChange={(event) => setRepetitions(event.target.value)}
                />
              </label>
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={pending?.tiny ?? tiny}
                  onChange={(event) => setTiny(event.target.checked)}
                />
                <span>Tiny functional test (not performance evidence)</span>
              </label>
            </div>
          </details>
        </fieldset>
        {datasetError && (
          <p className="request-error">
            {datasetError}{' '}
            <button type="button" onClick={retryDatasets}>
              Retry datasets
            </button>
          </p>
        )}
        <p className={compact ? 'matrix-label sr-only' : 'matrix-label'}>
          Execution matrix <span className="mono">1 · 2 · 4 · 8 workers</span>
        </p>
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        <button
          type="submit"
          className="primary run-button"
          disabled={disabled || (!selected && !pending)}
        >
          {pending ? 'Retry same submission' : '▷ Run experiment'}
        </button>
        <p className="fine-print">
          {compact
            ? 'Measured locally · full 1/2/4/8-worker matrix.'
            : 'One warm-up, then measured repetitions per configuration. Resize + grayscale uses Lanczos. Seed 2026 · sample interval 0.1 s. All four configurations run on this machine.'}
        </p>
      </form>
    </section>
  );
}
