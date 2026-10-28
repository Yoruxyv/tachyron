import {
  workers,
  type Dataset,
  type Distribution,
  type HistoryDetail,
  type HistoryItem,
  type Job,
  type JobState,
  type Metrics,
  type RunRequest,
  type WorkerSummary,
} from './contracts';

const invalid = () => new Error('The API returned an invalid response.');

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function object(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) throw invalid();
  return value;
}

function string(value: unknown): string {
  if (typeof value !== 'string') throw invalid();
  return value;
}

function number(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0)
    throw invalid();
  return value;
}

function integer(value: unknown): number {
  const result = number(value);
  if (!Number.isInteger(result)) throw invalid();
  return result;
}

function boolean(value: unknown): boolean {
  if (typeof value !== 'boolean') throw invalid();
  return value;
}

function nullable<T>(value: unknown, parse: (value: unknown) => T): T | null {
  return value === null ? null : parse(value);
}

export function array<T>(value: unknown, parse: (value: unknown) => T): T[] {
  if (!Array.isArray(value)) throw invalid();
  return value.map((item: unknown) => parse(item));
}

function timestamp(value: unknown): string {
  const result = string(value);
  if (!Number.isFinite(Date.parse(result))) throw invalid();
  return result;
}

/**
 * Read discovery metadata without treating a missing count as an empty dataset.
 * @param value Untrusted JSON from the dataset registry.
 * @returns Metadata consumed by the benchmark form.
 * @throws {Error} A required field is missing or has the wrong shape.
 */
export function parseDataset(value: unknown): Dataset {
  const data = object(value);
  return {
    dataset_id: string(data.dataset_id),
    input_count: nullable(data.input_count, integer),
    total_bytes: nullable(data.total_bytes, integer),
    manifest_sha256: nullable(data.manifest_sha256, string),
    error_code: nullable(data.error_code, string),
  };
}

/**
 * Decode both API request snapshots and persisted submission recovery data.
 * Server admission remains authoritative for aggregate pixel limits and discovery.
 * @param value Untrusted request document.
 * @returns Configuration for the fixed resize/grayscale pipeline.
 * @throws {Error} A field is missing, invalid, or outside the public API bounds.
 */
export function parseRequest(value: unknown): RunRequest {
  const data = object(value);
  if (data.operation !== 'resize-grayscale-v1') throw invalid();
  const request: RunRequest = {
    dataset_id: string(data.dataset_id),
    operation: data.operation,
    width: integer(data.width),
    height: integer(data.height),
    repetitions: integer(data.repetitions),
    seed: integer(data.seed),
    sampling_interval: number(data.sampling_interval),
    tiny: boolean(data.tiny),
  };
  if (
    request.width < 1 ||
    request.width > 4096 ||
    request.height < 1 ||
    request.height > 4096 ||
    request.repetitions < (request.tiny ? 1 : 5) ||
    request.repetitions > 20 ||
    request.seed > 2 ** 32 - 1 ||
    request.sampling_interval < 0.01 ||
    request.sampling_interval > 10
  ) {
    throw invalid();
  }
  return request;
}

function state(value: unknown): JobState {
  if (
    value === 'QUEUED' ||
    value === 'RUNNING' ||
    value === 'COMPLETED' ||
    value === 'FAILED' ||
    value === 'CANCELLED'
  )
    return value;
  throw invalid();
}

/**
 * Validate durable state and progress accounting before displaying it.
 * @param value Untrusted job snapshot.
 * @returns A known lifecycle state with bounded batch progress and explicit nulls.
 * @throws {Error} Identity, timestamps, field shapes, or batch accounting are invalid.
 */
export function parseJob(value: unknown): Job {
  const data = object(value);
  const id = string(data.id);
  if (!/^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/iu.test(id)) throw invalid();
  const job = {
    id,
    request: parseRequest(data.request),
    state: state(data.state),
    manifest_sha256: string(data.manifest_sha256),
    input_count: integer(data.input_count),
    created_at: timestamp(data.created_at),
    started_at: nullable(data.started_at, timestamp),
    ended_at: nullable(data.ended_at, timestamp),
    cancel_requested: boolean(data.cancel_requested),
    completed_batches: integer(data.completed_batches),
    total_batches: integer(data.total_batches),
    succeeded_images: integer(data.succeeded_images),
    failed_images: integer(data.failed_images),
    unknown_batches: integer(data.unknown_batches),
    error_code: nullable(data.error_code, string),
  };
  if (job.total_batches < 1 || job.completed_batches > job.total_batches)
    throw invalid();
  return job;
}

function distribution(value: unknown): Distribution {
  const data = object(value);
  return {
    count: integer(data.count),
    mean: nullable(data.mean, number),
    median: nullable(data.median, number),
    minimum: nullable(data.minimum, number),
    maximum: nullable(data.maximum, number),
    sample_stddev: nullable(data.sample_stddev, number),
  };
}

function summary(value: unknown): WorkerSummary {
  const data = object(value);
  const worker = workers.find((count) => count === data.workers);
  if (worker === undefined) throw invalid();
  return {
    workers: worker,
    measured_count: integer(data.measured_count),
    complete: boolean(data.complete),
    elapsed: nullable(data.elapsed, distribution),
    mean_throughput: nullable(data.mean_throughput, number),
    speedup: nullable(data.speedup, number),
    efficiency_percent: nullable(data.efficiency_percent, number),
    cpu_mean_core_percent: nullable(data.cpu_mean_core_percent, number),
    rss_peak_mib: nullable(data.rss_peak_mib, number),
    cpu_runs_available: integer(data.cpu_runs_available),
    memory_runs_available: integer(data.memory_runs_available),
  };
}

/**
 * Preserve server statistics while requiring one entry per worker configuration.
 * @param value Untrusted summary collection.
 * @returns Summaries ordered by 1, 2, 4, and 8 workers; missing observations stay null.
 * @throws {Error} A configuration is missing, duplicated, unsupported, or malformed.
 */
export function parseSummaries(value: unknown): WorkerSummary[] {
  const summaries = array(value, summary).sort(
    (left, right) => left.workers - right.workers,
  );
  if (
    summaries.length !== workers.length ||
    summaries.some((item, index) => item.workers !== workers[index])
  )
    throw invalid();
  return summaries;
}

/**
 * Decode the versioned fields the dashboard consumes, without recomputing evidence.
 * The raw API link retains the complete experiment, records, and provenance.
 * @param value Untrusted metrics document.
 * @returns Version, context identity, summaries, and environment presentation fields.
 * @throws {Error} The schema version or any consumed field is invalid.
 */
export function parseMetrics(value: unknown): Metrics {
  const data = object(value);
  if (data.schema_version !== 1) throw invalid();
  const experiment = object(data.experiment);
  const context = object(experiment.context);
  const environment = object(context.environment);
  return {
    schema_version: data.schema_version,
    context_id: string(data.context_id),
    summaries: parseSummaries(data.summaries),
    experiment: {
      status: string(experiment.status),
      context: {
        tiny: boolean(context.tiny),
        environment: {
          cpu_model: nullable(environment.cpu_model, string),
          logical_cpus: nullable(environment.logical_cpus, integer),
          os_name: string(environment.os_name),
          python_version: string(environment.python_version),
        },
      },
    },
  };
}

export function parseHistoryItem(value: unknown): HistoryItem {
  const data = object(value);
  return {
    job: parseJob(data.job),
    summaries: nullable(data.summaries, parseSummaries),
  };
}

export function parseHistoryDetail(value: unknown): HistoryDetail {
  const data = object(value);
  return {
    job: parseJob(data.job),
    result: nullable(data.result, parseMetrics),
  };
}
