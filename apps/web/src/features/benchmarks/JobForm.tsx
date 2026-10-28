import { useId, useState, type SubmitEvent } from 'react';

import { type Dataset, type RunRequest } from '@/api/contracts';

interface Props {
  readonly datasets: Dataset[];
  readonly disabled: boolean;
  readonly pending: RunRequest | undefined;
  readonly submit: (request: RunRequest) => Promise<void>;
}
export default function JobForm({
  datasets,
  disabled,
  pending,
  submit,
}: Props) {
  const id = useId();
  const [datasetId, setDatasetId] = useState('');
  const [width, setWidth] = useState('256');
  const [height, setHeight] = useState('256');
  const [repetitions, setRepetitions] = useState('5');
  const [tiny, setTiny] = useState(false);
  const [error, setError] = useState('');
  const selected =
    pending?.dataset_id ??
    (datasetId ||
      datasets.find(
        (dataset) =>
          dataset.error_code === null && (dataset.input_count ?? 0) > 0,
      )?.dataset_id ||
      '');
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
      return;
    }
    setError('');
    submit(request).catch(() =>
      setError('Unable to submit. Retry with the same configuration.'),
    );
  }
  return (
    <section className="card quick-card" aria-labelledby={`${id}-title`}>
      <div className="panel-heading">
        <div>
          <h2 id={`${id}-title`}>Quick experiment</h2>
          <p>Run a real image-processing comparison</p>
        </div>
        <span aria-hidden="true">⚗</span>
      </div>
      <form onSubmit={handleSubmit} noValidate>
        <fieldset disabled={disabled || Boolean(pending)}>
          <label htmlFor={`${id}-operation`}>Processing operation</label>
          <select
            id={`${id}-operation`}
            value="resize-grayscale-v1"
            onChange={() => {}}
          >
            <option value="resize-grayscale-v1">
              Resize + grayscale · Lanczos
            </option>
          </select>
          <label htmlFor={`${id}-dataset`}>Dataset</label>
          <select
            id={`${id}-dataset`}
            value={selected}
            onChange={(event) => setDatasetId(event.target.value)}
          >
            <option value="">Choose a registered dataset</option>
            {datasets.map((dataset) => (
              <option
                key={dataset.dataset_id}
                value={dataset.dataset_id}
                disabled={dataset.error_code !== null || !dataset.input_count}
              >
                {dataset.dataset_id} · {dataset.input_count ?? 'unavailable'}{' '}
                images{dataset.error_code ? ' · unavailable' : ''}
              </option>
            ))}
          </select>
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
        </fieldset>
        <p className="matrix-label">
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
          One warm-up, then measured repetitions per configuration. Seed 2026 ·
          sample interval 0.1 s. All four configurations run on this machine.
        </p>
      </form>
    </section>
  );
}
