import {
  workers,
  type Dataset,
  type HistoryDetail,
  type Job,
  type JobState,
  type Metrics,
  type RunRequest,
  type WorkerSummary,
} from '@/api/contracts';

// Synthetic contract fixtures are confined to tests; production has no seed data.
export const request: RunRequest = {
  dataset_id: 'test-images',
  operation: 'resize-grayscale-v1',
  width: 256,
  height: 256,
  repetitions: 5,
  seed: 2026,
  sampling_interval: 0.1,
  tiny: false,
};
export const dataset: Dataset = {
  dataset_id: request.dataset_id,
  input_count: 8,
  total_bytes: 1024,
  manifest_sha256: 'a'.repeat(64),
  error_code: null,
};

export function job(
  state: JobState = 'QUEUED',
  id = '00000000-0000-4000-8000-000000000001',
): Job {
  return {
    id,
    request,
    manifest_sha256: 'a'.repeat(64),
    input_count: 8,
    state,
    created_at: '2026-10-27T00:00:00+00:00',
    started_at: null,
    ended_at: null,
    cancel_requested: false,
    completed_batches: state === 'COMPLETED' ? 24 : 0,
    total_batches: 24,
    succeeded_images: 0,
    failed_images: 0,
    unknown_batches: 0,
    error_code: state === 'FAILED' ? 'batch_failed' : null,
  };
}

export const summaries: WorkerSummary[] = workers.map((count) => ({
  workers: count,
  measured_count: 5,
  complete: true,
  elapsed: {
    count: 5,
    mean: 8 / count,
    median: 8 / count,
    minimum: 7 / count,
    maximum: 9 / count,
    sample_stddev: 0.1,
  },
  mean_throughput: count,
  speedup: count,
  efficiency_percent: 100,
  cpu_mean_core_percent: 70 * count,
  rss_peak_mib: 200 * count,
  cpu_runs_available: 5,
  memory_runs_available: 5,
}));
export const metrics: Metrics & { schema_version: 1 } = {
  schema_version: 1,
  context_id: 'b'.repeat(64),
  summaries,
  experiment: {
    status: 'success',
    context: {
      tiny: false,
      environment: {
        cpu_model: 'Fixture CPU',
        logical_cpus: 8,
        os_name: 'Fixture OS',
        python_version: '3.11',
      },
    },
  },
};
export const detail: HistoryDetail = { job: job('COMPLETED'), result: metrics };
