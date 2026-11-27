import { describe, expect, it, vi } from 'vitest';

import {
  cancelOperation,
  getOperation,
  parseOperation,
  startOperation,
} from '@/api/operations';

import {
  observation,
  operation,
  otherRunId,
  runId,
} from './operation-fixtures';

describe('C2 dashboard writes', () => {
  const input = {
    dataset_id: 'browser-test',
    width: 16,
    height: 12,
    mode: 'manual',
    workers: 2,
  } as const;

  it('treats an inconsistent admission response as unverified', async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockResolvedValue(
        Response.json(operation({ requested_workers: 4 }), { status: 202 }),
      );
    vi.stubGlobal('fetch', fetch);
    await expect(
      startOperation(input, new AbortController().signal),
    ).rejects.toMatchObject({ status: 0 });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('requires server evidence of cancellation or a terminal outcome', async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockResolvedValueOnce(Response.json(operation(), { status: 202 }))
      .mockResolvedValueOnce(
        Response.json(operation({ cancel_requested: true }), { status: 202 }),
      );
    vi.stubGlobal('fetch', fetch);
    const signal = new AbortController().signal;
    await expect(cancelOperation(runId, signal)).rejects.toMatchObject({
      status: 0,
    });
    await expect(cancelOperation(runId, signal)).resolves.toMatchObject({
      cancel_requested: true,
      state: 'RUNNING',
    });
    expect(fetch).toHaveBeenCalledWith(
      `/api/operations/${runId}/cancel`,
      expect.objectContaining({
        method: 'POST',
        headers: { Accept: 'application/json', 'X-Tachyron-Request': '1' },
      }),
    );
  });
});

describe('C2 snapshot decoding', () => {
  it('sends cursor-scoped reads with request security and a cancellation signal', async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockResolvedValue(Response.json(operation({ events: [] })));
    vi.stubGlobal('fetch', fetch);
    const controller = new AbortController();
    const result = await getOperation(runId, 1, controller.signal);
    expect(result.progress.pending).toBe(8);
    expect(result.events).toEqual([]);
    expect(fetch).toHaveBeenCalledWith(
      `/api/operations/${runId}?after=1`,
      expect.objectContaining({
        cache: 'no-store',
        headers: { Accept: 'application/json', 'X-Tachyron-Request': '1' },
        signal: expect.any(AbortSignal),
      }),
    );
    controller.abort();
    expect(fetch.mock.calls[0]?.[1]?.signal?.aborted).toBe(true);
  });

  it.each([
    { run_id: otherRunId },
    { schema_version: 2 },
    {
      progress: {
        total: 8,
        pending: 7,
        succeeded: 2,
        failed: 0,
        unknown: 0,
        not_started: 0,
      },
    },
    { snapshot_sequence: Number.MAX_SAFE_INTEGER + 1 },
    { events: [observation(2)] },
    { pool: { requested_workers: 2, observed_workers: [{ pid: 1 }] } },
    { resync_required: true },
  ])(
    'rejects incompatible snapshots instead of displaying partial state (%j)',
    (field) => {
      expect(() => parseOperation(operation(field), runId, null)).toThrow();
    },
  );

  it.each([1, 300])(
    'accepts a bounded reconciliation window from cursor %i',
    (after) => {
      const events = Array.from({ length: 256 }, (_, index) =>
        observation(index + 5),
      );
      const result = parseOperation(
        operation({
          snapshot_sequence: 260,
          first_available_sequence: 5,
          event_window_evictions: 4,
          resync_required: true,
          events,
        }),
        runId,
        after,
      );
      expect(result.events).toHaveLength(256);
      expect(result.events[0]?.sequence).toBe(5);
    },
  );

  it('keeps transport uncertainty independent of the server event window', () => {
    const result = parseOperation(
      operation({
        transport_loss_count_unquantified: true,
        coverage: 'best_effort_with_gaps',
      }),
      runId,
      null,
    );
    expect(result.transport_loss_count_unquantified).toBe(true);
    expect(result.transport_dropped_lower_bound).toBe(0);
    expect(result.event_window_evictions).toBe(0);
  });
});
