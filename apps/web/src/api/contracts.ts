export const workers = [1, 2, 4, 8] as const;
export type WorkerCount = (typeof workers)[number];
export type JobState =
  'QUEUED' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELLED';
export interface RunRequest {
  dataset_id: string;
  operation: 'resize-grayscale-v1';
  width: number;
  height: number;
  repetitions: number;
  seed: number;
  sampling_interval: number;
  tiny: boolean;
}
export interface Dataset {
  dataset_id: string;
  input_count: number | null;
  total_bytes: number | null;
  manifest_sha256: string | null;
  error_code: string | null;
}
export interface Job {
  id: string;
  request: RunRequest;
  manifest_sha256: string;
  input_count: number;
  state: JobState;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
  cancel_requested: boolean;
  completed_batches: number;
  total_batches: number;
  succeeded_images: number;
  failed_images: number;
  unknown_batches: number;
  error_code: string | null;
}
export interface Distribution {
  count: number;
  mean: number | null;
  median: number | null;
  minimum: number | null;
  maximum: number | null;
  sample_stddev: number | null;
}
export interface WorkerSummary {
  workers: WorkerCount;
  measured_count: number;
  complete: boolean;
  elapsed: Distribution | null;
  mean_throughput: number | null;
  speedup: number | null;
  efficiency_percent: number | null;
  cpu_mean_core_percent: number | null;
  rss_peak_mib: number | null;
  cpu_runs_available: number;
  memory_runs_available: number;
}
export interface Metrics {
  schema_version: 1;
  context_id: string;
  summaries: WorkerSummary[];
  experiment: {
    status: string;
    context: {
      tiny: boolean;
      environment: {
        cpu_model: string | null;
        logical_cpus: number | null;
        os_name: string;
        python_version: string;
      };
    };
  };
}
export interface HistoryItem {
  job: Job;
  summaries: WorkerSummary[] | null;
}
export interface HistoryDetail {
  job: Job;
  result: Metrics | null;
}
export const isTerminal = (state: JobState) =>
  state !== 'QUEUED' && state !== 'RUNNING';
