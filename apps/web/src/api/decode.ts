import {
  maxCustomConfigurations,
  maxCustomWorkerCount,
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

export const invalid = () => new Error('The API returned an invalid response.');

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function object(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) throw invalid();
  return value;
}

export function string(value: unknown): string {
  if (typeof value !== 'string') throw invalid();
  return value;
}

function number(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0)
    throw invalid();
  return value;
}

export function integer(value: unknown): number {
  const result = number(value);
  if (!Number.isInteger(result)) throw invalid();
  return result;
}

export function boolean(value: unknown): boolean {
  if (typeof value !== 'boolean') throw invalid();
  return value;
}

export function nullable<T>(
  value: unknown,
  parse: (value: unknown) => T,
): T | null {
  return value === null ? null : parse(value);
}

export function array<T>(value: unknown, parse: (value: unknown) => T): T[] {
  if (!Array.isArray(value)) throw invalid();
  return value.map((item: unknown) => parse(item));
}

export function isBenchmarkId(value: string) {
  return /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/iu.test(value);
}

function benchmarkId(value: unknown) {
  const id = string(value);
  if (!isBenchmarkId(id)) throw invalid();
  return id;
}

export function parseWorkerCounts(value: unknown): number[] {
  if (
    !Array.isArray(value) ||
    value.length < 1 ||
    value.length > maxCustomConfigurations
  )
    throw invalid();
  const counts = array(value, integer).sort((left, right) => left - right);
  if (
    counts.some((count) => count < 1 || count > maxCustomWorkerCount) ||
    new Set(counts).size !== counts.length
  )
    throw invalid();
  return counts;
}

export function timestamp(value: unknown): string {
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
    ...(data.display_name !== undefined
      ? { display_name: nullable(data.display_name, string) }
      : {}),
    ...(data.source !== undefined ? { source: string(data.source) } : {}),
    ...(data.selection_mode !== undefined
      ? { selection_mode: nullable(data.selection_mode, string) }
      : {}),
    ...(data.excluded_count !== undefined
      ? { excluded_count: integer(data.excluded_count) }
      : {}),
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
    ...(data.worker_counts === undefined
      ? {}
      : { worker_counts: parseWorkerCounts(data.worker_counts) }),
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
  const id = benchmarkId(data.id);
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
  if (
    job.request.worker_counts &&
    job.total_batches !==
      job.request.worker_counts.length * (job.request.repetitions + 1)
  )
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
  const worker = integer(data.workers);
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
 * @param counts Declared worker counts in numeric order, defaulting to the v1 matrix.
 * @returns Ordered summaries with missing observations preserved as null.
 * @throws {Error} A configuration is missing, duplicated, unsupported, or malformed.
 */
export function parseSummaries(
  value: unknown,
  counts: readonly number[] = workers,
): WorkerSummary[] {
  const summaries = array(value, summary).sort(
    (left, right) => left.workers - right.workers,
  );
  if (
    summaries.length !== counts.length ||
    summaries.some((item, index) => item.workers !== counts[index])
  )
    throw invalid();
  return summaries;
}

function validMeasuredRounds(summary: WorkerSummary, repetitions: number) {
  const { measured_count: count, elapsed } = summary;
  if (
    count > repetitions ||
    summary.complete !== (count === repetitions) ||
    summary.cpu_runs_available > count ||
    summary.memory_runs_available > count
  )
    return false;
  if (count === 0) return elapsed === null;
  return elapsed?.count === count && (elapsed.mean ?? 0) > 0;
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
  if (data.schema_version !== 1 && data.schema_version !== 2) throw invalid();
  const experiment = object(data.experiment);
  const counts =
    data.schema_version === 2
      ? parseWorkerCounts(object(experiment.config).worker_counts)
      : workers;
  const summaries = parseSummaries(data.summaries, counts);
  const status = string(experiment.status);
  const baseline = summaries.find((row) => row.workers === 1 && row.complete);
  if (
    data.schema_version === 2 &&
    (status !== 'success' || !baseline) &&
    summaries.some(
      (summary) =>
        summary.speedup !== null || summary.efficiency_percent !== null,
    )
  )
    throw invalid();
  const context = object(experiment.context);
  const environment = object(context.environment);
  return {
    schema_version: data.schema_version,
    context_id: string(data.context_id),
    summaries,
    experiment: {
      ...(experiment.experiment_id !== undefined || data.schema_version === 2
        ? { experiment_id: benchmarkId(experiment.experiment_id) }
        : {}),
      status,
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
  const job = parseJob(data.job);
  return {
    job,
    summaries: nullable(data.summaries, (summaries) =>
      parseSummaries(summaries, job.request.worker_counts),
    ),
  };
}

export function parseHistoryDetail(value: unknown): HistoryDetail {
  const data = object(value);
  const job = parseJob(data.job);
  const result = nullable(data.result, parseMetrics);
  if (result) {
    const requested = job.request.worker_counts;
    if (result.schema_version !== (requested ? 2 : 1)) throw invalid();
    if (
      requested &&
      (result.summaries.length !== requested.length ||
        result.summaries.some(
          (summary, index) =>
            summary.workers !== requested[index] ||
            !validMeasuredRounds(summary, job.request.repetitions),
        ))
    )
      throw invalid();
  }
  return { job, result };
}
