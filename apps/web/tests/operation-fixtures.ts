export const runId = 'fc146111-abcf-4320-93bf-7ca35e2f4ab0';
export const otherRunId = 'fc146111-abcf-4320-93bf-7ca35e2f4ab1';
export const poolId = 'bc146111-abcf-4320-93bf-7ca35e2f4ab0';
export const instanceId = 'cc146111abcf432093bf7ca35e2f4ab0';

export function observation(
  sequence: number,
  fields: Record<string, unknown> = {},
) {
  return {
    sequence,
    version: 1,
    type: 'operation_started',
    origin: 'parent',
    phase: 'preparing',
    ...fields,
  };
}

export function operation(fields: Record<string, unknown> = {}) {
  return {
    schema_version: 1,
    run_id: runId,
    dataset_id: 'browser-test',
    mode: 'manual',
    requested_workers: 2,
    state: 'RUNNING',
    phase: 'preparing',
    cancel_requested: false,
    error_code: null,
    progress: {
      total: 8,
      pending: 8,
      succeeded: 0,
      failed: 0,
      unknown: 0,
      not_started: 0,
    },
    pool: null,
    selection: null,
    calibration_trials_finished: 0,
    final_execution_id: null,
    observed_at: '2026-04-11T00:00:00Z',
    snapshot_sequence: 1,
    first_available_sequence: 1,
    resync_required: false,
    event_window_evictions: 0,
    transport_dropped_lower_bound: 0,
    transport_loss_count_unquantified: false,
    transport_unavailable: false,
    coverage: 'best_effort',
    events: [observation(1)],
    ...fields,
  };
}
