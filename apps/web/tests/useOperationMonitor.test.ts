import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/api/benchmarks';
import * as api from '@/api/operations';
import {
  reconcileOperation,
  useOperationMonitor,
} from '@/features/operations/useOperationMonitor';

import { observation, operation, runId } from './operation-fixtures';

const running = api.parseOperation(operation(), runId, null);
const completed = api.parseOperation(
  operation({
    state: 'COMPLETED',
    phase: 'execution',
    progress: {
      total: 8,
      pending: 0,
      succeeded: 8,
      failed: 0,
      unknown: 0,
      not_started: 0,
    },
    snapshot_sequence: 2,
    events: [observation(2, { type: 'operation_terminal' })],
  }),
  runId,
  1,
);

beforeEach(() => vi.useFakeTimers());

const tick = async (ms = 0) => {
  await act(async () => vi.advanceTimersByTimeAsync(ms));
};

describe('operation polling', () => {
  it('merges incremental events using the latest counts and stops after terminal reconciliation', async () => {
    const get = vi
      .spyOn(api, 'getOperation')
      .mockResolvedValueOnce(running)
      .mockResolvedValueOnce(completed);
    const { result } = renderHook(() => useOperationMonitor(runId));
    await tick();
    expect(result.current.connection).toBe('live');
    expect(result.current.animateAfter).toBeNull();
    await tick(800);
    expect(get).toHaveBeenLastCalledWith(runId, 1, expect.any(AbortSignal));
    expect(result.current.connection).toBe('terminal');
    expect(result.current.animateAfter).toBe(1);
    expect(result.current.snapshot?.progress.succeeded).toBe(8);
    expect(
      result.current.snapshot?.events.map((entry) => entry.sequence),
    ).toEqual([1, 2]);
    await tick(60000);
    expect(get).toHaveBeenCalledTimes(2);
  });

  it('keeps stale data marked during a stalled request, uses one request, and aborts on unmount', async () => {
    let resolve: (value: api.OperationSnapshot) => void = () => {};
    const get = vi
      .spyOn(api, 'getOperation')
      .mockResolvedValueOnce(running)
      .mockImplementationOnce(
        () =>
          new Promise((finish) => {
            resolve = finish;
          }),
      )
      .mockResolvedValue(completed);
    const { result, unmount } = renderHook(() => useOperationMonitor(runId));
    await tick(5000);
    expect(get).toHaveBeenCalledTimes(2);
    expect(result.current.connection).toBe('stale');
    expect(result.current.snapshot?.progress.pending).toBe(8);
    const previousSignal = get.mock.calls[1]?.[2];
    unmount();
    expect(previousSignal?.aborted).toBe(true);
    await act(async () => resolve(completed));
    expect(get).toHaveBeenCalledTimes(2);
  });

  it('honors Retry-After and replaces a missed window with no animated history', async () => {
    const recovered = api.parseOperation(
      operation({
        resync_required: true,
        snapshot_sequence: 4,
        first_available_sequence: 4,
        event_window_evictions: 3,
        events: [observation(4)],
      }),
      runId,
      1,
    );
    const get = vi
      .spyOn(api, 'getOperation')
      .mockResolvedValueOnce(running)
      .mockRejectedValueOnce(new ApiError('Retry later', 503, 7000))
      .mockResolvedValueOnce(recovered);
    const { result } = renderHook(() => useOperationMonitor(runId));
    await tick(800);
    expect(result.current.connection).toBe('stale');
    await tick(6999);
    expect(get).toHaveBeenCalledTimes(2);
    await tick(1);
    expect(result.current.connection).toBe('live');
    expect(result.current.animateAfter).toBeNull();
    expect(result.current.message).toMatch(/window refreshed/);
    expect(
      result.current.snapshot?.events.map((entry) => entry.sequence),
    ).toEqual([4]);
  });

  it('stops on unavailable snapshots and restarts with a manual retry', async () => {
    const get = vi
      .spyOn(api, 'getOperation')
      .mockRejectedValueOnce(new ApiError('Unavailable', 404))
      .mockResolvedValueOnce(running);
    const { result, unmount } = renderHook(() => useOperationMonitor(runId));
    await tick(30000);
    expect(result.current.connection).toBe('unavailable');
    expect(get).toHaveBeenCalledTimes(1);
    act(() => result.current.retry());
    await tick();
    expect(result.current.connection).toBe('live');
    unmount();
    expect(get.mock.calls[1]?.[2].aborted).toBe(true);
  });

  it('prunes the client window even when a continuous cursor is still valid', () => {
    const previous = api.parseOperation(
      operation({
        snapshot_sequence: 256,
        events: Array.from({ length: 256 }, (_, index) =>
          observation(index + 1),
        ),
      }),
      runId,
      null,
    );
    const next = api.parseOperation(
      operation({
        snapshot_sequence: 257,
        first_available_sequence: 2,
        event_window_evictions: 1,
        events: [observation(257)],
      }),
      runId,
      256,
    );
    const view = reconcileOperation(previous, next);
    expect(view.events).toHaveLength(256);
    expect(view.events[0]?.sequence).toBe(2);
    expect(view.events[255]?.sequence).toBe(257);
  });
});
