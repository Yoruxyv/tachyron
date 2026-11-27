import { useId, useState, type ReactNode, type SubmitEvent } from 'react';

import {
  maxCustomConfigurations,
  maxCustomWorkerCount,
  workers,
  type Dataset,
  type RunRequest,
  type WorkerCount,
} from '@/api/contracts';
import Select from '@/components/Select';
import { parseWorkerCounts } from '@/api/decode';
import {
  ManualWorkers,
  ModeField,
  type ExecutionMode,
} from '@/features/operations/OperationControls';
import type { useOperationalExecution } from '@/features/operations/useOperationalExecution';

import DatasetSelect from '@/features/datasets/components/DatasetSelect';
import WorkerSelector from '../results/WorkerSelector';

// Browser defaults and hints mirror RunRequest; the server validates every submission.
const defaults: Omit<RunRequest, 'dataset_id'> = {
  operation: 'resize-grayscale-v1',
  width: 256,
  height: 256,
  repetitions: 5,
  seed: 2026,
  sampling_interval: 0.1,
  tiny: false,
};
const dimensions = { minimum: 1, maximum: 4096 };
const repetitionsRange = { minimum: 5, tinyMinimum: 1, maximum: 20 };

interface ModeControls {
  readonly value: ExecutionMode;
  readonly select: (mode: ExecutionMode) => void;
  readonly execution: ReturnType<typeof useOperationalExecution>;
}

function validDimension(value: number) {
  return (
    Number.isInteger(value) &&
    value >= dimensions.minimum &&
    value <= dimensions.maximum
  );
}

function validRepetitions(request: RunRequest) {
  const minimum = request.tiny
    ? repetitionsRange.tinyMinimum
    : repetitionsRange.minimum;
  return (
    Number.isInteger(request.repetitions) &&
    request.repetitions >= minimum &&
    request.repetitions <= repetitionsRange.maximum
  );
}

function submitLabel(mode: ExecutionMode, pending: RunRequest | undefined) {
  if (mode === 'manual') return 'Start manual run';
  if (mode === 'adaptive') return 'Start adaptive run';
  return pending ? 'Retry same submission' : '▷ Run experiment';
}

interface Props {
  readonly importAction?: ReactNode;
  readonly controls?: ModeControls;
  readonly datasets: Dataset[];
  readonly disabled: boolean;
  readonly pending: RunRequest | undefined;
  readonly submit: (request: RunRequest) => Promise<void>;
  readonly datasetId: string;
  readonly selectDataset: (id: string) => void;
  readonly compact?: boolean;
  readonly selectedWorker?: number;
  readonly inspectionCounts?: readonly number[];
  readonly selectWorker?: (count: number) => void;
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
  inspectionCounts = workers,
  selectWorker,
  datasetError = '',
  retryDatasets,
  importAction,
  controls,
}: Props) {
  const id = useId();
  const mode = controls?.value ?? 'benchmark';
  const benchmark = mode === 'benchmark';
  const operation = controls?.execution;
  const blocked = !benchmark && operation?.uncertain;
  const fieldsDisabled = disabled || Boolean(pending);
  const [workerCount, setWorkerCount] = useState<WorkerCount>(1);
  const [customCounts, setCustomCounts] = useState('');
  const [width, setWidth] = useState(String(defaults.width));
  const [height, setHeight] = useState(String(defaults.height));
  const [repetitions, setRepetitions] = useState(String(defaults.repetitions));
  const [tiny, setTiny] = useState(defaults.tiny);
  const [error, setError] = useState('');
  const [settingsOpen, setSettingsOpen] = useState(!compact);
  const selected = pending?.dataset_id ?? datasetId;
  const shownCounts = pending
    ? (pending.worker_counts?.join(', ') ?? '')
    : customCounts;
  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (disabled || blocked) return;
    let request: RunRequest = pending ?? {
      ...defaults,
      dataset_id: selected,
      width: Number(width),
      height: Number(height),
      repetitions: Number(repetitions),
      tiny,
    };
    if (benchmark && !compact && !pending && customCounts.trim()) {
      try {
        const values = customCounts
          .split(',')
          .map((value) => (/^\d+$/u.test(value.trim()) ? Number(value) : NaN));
        request = { ...request, worker_counts: parseWorkerCounts(values) };
      } catch {
        setError(
          `Enter up to ${maxCustomConfigurations} distinct worker counts from 1 to ${maxCustomWorkerCount}, separated by commas.`,
        );
        setSettingsOpen(true);
        return;
      }
    }
    const dataset = datasets.find(
      (item) => item.dataset_id === request.dataset_id,
    );
    if (
      !pending &&
      (dataset?.error_code !== null ||
        !dataset.input_count ||
        !validDimension(request.width) ||
        !validDimension(request.height) ||
        (benchmark && !validRepetitions(request)))
    ) {
      setError(
        benchmark
          ? `Choose an available dataset, dimensions from ${dimensions.minimum} to ${dimensions.maximum}, and ${repetitionsRange.minimum}–${repetitionsRange.maximum} measured repetitions (${repetitionsRange.tinyMinimum}–${repetitionsRange.maximum} for a tiny functional test).`
          : 'Choose an available dataset and dimensions from 1 to 4096 pixels.',
      );
      setSettingsOpen(true);
      return;
    }
    setError('');
    if (mode !== 'benchmark' && operation) {
      const { dataset_id, width: outputWidth, height: outputHeight } = request;
      const input = { dataset_id, width: outputWidth, height: outputHeight };
      void operation.start(
        mode === 'manual'
          ? { ...input, mode, workers: workerCount }
          : { ...input, mode },
      );
      return;
    }
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
          <p>
            {benchmark
              ? 'Configure a measured benchmark run'
              : 'Process a registered dataset once'}
          </p>
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
        <fieldset disabled={fieldsDisabled}>
          {controls && (
            <ModeField
              id={`${id}-mode`}
              mode={mode}
              disabled={fieldsDisabled}
              select={(value) => {
                setError('');
                controls.select(value);
              }}
            />
          )}
          <div className="form-field">
            <label htmlFor={`${id}-operation`}>Processing operation</label>
            <Select
              id={`${id}-operation`}
              label="Processing operation"
              value={defaults.operation}
              onChange={() => undefined}
              disabled={disabled || Boolean(pending)}
              options={[
                { value: defaults.operation, label: 'Resize + Grayscale' },
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
              <WorkerSelector
                value={selectedWorker}
                counts={inspectionCounts}
                onChange={selectWorker}
              />
            )}
          </div>
          {mode === 'manual' && (
            <ManualWorkers
              id={`${id}-workers`}
              count={workerCount}
              disabled={fieldsDisabled}
              select={setWorkerCount}
            />
          )}
          <details
            className="benchmark-settings"
            open={settingsOpen}
            onToggle={(event) => setSettingsOpen(event.currentTarget.open)}
          >
            <summary>
              {benchmark ? 'Benchmark settings' : 'Output settings'}{' '}
              <span className="mono">
                {pending?.width ?? width} × {pending?.height ?? height}
                {benchmark && ` · ${pending?.repetitions ?? repetitions} runs`}
                {benchmark && (pending?.tiny ?? tiny) && ' · tiny test'}
              </span>
            </summary>
            <div className="settings-content">
              <div className="form-columns">
                <label>
                  <span>Width (px)</span>
                  <input
                    type="number"
                    min={dimensions.minimum}
                    max={dimensions.maximum}
                    required
                    value={pending?.width ?? width}
                    onChange={(event) => setWidth(event.target.value)}
                  />
                </label>
                <label>
                  <span>Height (px)</span>
                  <input
                    type="number"
                    min={dimensions.minimum}
                    max={dimensions.maximum}
                    required
                    value={pending?.height ?? height}
                    onChange={(event) => setHeight(event.target.value)}
                  />
                </label>
              </div>
              {benchmark && (
                <>
                  {!compact && (
                    <label>
                      <span>Custom worker counts</span>
                      <input
                        type="text"
                        value={shownCounts}
                        aria-describedby={`${id}-custom-counts`}
                        maxLength={60}
                        placeholder="1, 2, 4, 8"
                        onChange={(event) =>
                          setCustomCounts(event.target.value)
                        }
                      />
                      <small id={`${id}-custom-counts`} className="fine-print">
                        Leave blank for 1, 2, 4 and 8. Enter up to{' '}
                        {maxCustomConfigurations} distinct counts from 1 to{' '}
                        {maxCustomWorkerCount}, separated by commas. Host limits
                        apply.
                      </small>
                    </label>
                  )}
                  <label>
                    <span>Measured repetitions per worker</span>
                    <input
                      type="number"
                      min={
                        tiny
                          ? repetitionsRange.tinyMinimum
                          : repetitionsRange.minimum
                      }
                      max={repetitionsRange.maximum}
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
                </>
              )}
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
        {benchmark && (
          <p className={compact ? 'matrix-label sr-only' : 'matrix-label'}>
            Execution matrix{' '}
            <span className="mono">
              {pending || compact
                ? (pending?.worker_counts ?? workers).join(' · ')
                : shownCounts.trim() || workers.join(' · ')}{' '}
              workers
            </span>
          </p>
        )}
        {mode === 'adaptive' && (
          <p className="fine-print">
            Experimental adaptive mode spends time calibrating before
            processing. It may fall back to a safe worker count. Faster
            execution is not guaranteed.
          </p>
        )}
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        <button
          type="submit"
          className="primary run-button"
          disabled={disabled || blocked || (!selected && !pending)}
        >
          {submitLabel(mode, pending)}
        </button>
        {benchmark && (
          <p className="fine-print">
            {compact && !pending?.worker_counts
              ? `Measured locally · full ${workers.join('/')}-worker matrix.`
              : `One local warm-up, then measured repetitions per configuration. Resize + grayscale uses Lanczos. Seed ${pending?.seed ?? defaults.seed} · sample interval ${pending?.sampling_interval ?? defaults.sampling_interval} s.`}
          </p>
        )}
      </form>
    </section>
  );
}
