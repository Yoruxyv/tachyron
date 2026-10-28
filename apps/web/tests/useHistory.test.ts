import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import * as api from '@/api/benchmarks';
import type { HistoryDetail } from '@/api/contracts';
import { useHistory } from '@/features/history/hooks/useHistory';

import { detail, job } from './fixtures';

describe('history selection races', () => {
  it('keeps loaded evidence mounted during a refresh and retains its identity for identical replies', async () => {
    const records = [
      { job: detail.job, summaries: detail.result?.summaries ?? null },
    ];
    const list = vi.spyOn(api, 'getHistory').mockResolvedValue(records);
    const evidence = vi
      .spyOn(api, 'getDetail')
      .mockResolvedValue(structuredClone(detail));
    const { result } = renderHook(() => useHistory(null));
    await waitFor(() => expect(result.current.detail).not.toBeNull());
    const previous = result.current.detail;
    let resolveList: ((value: typeof records) => void) | undefined;
    let resolveDetail: ((value: HistoryDetail) => void) | undefined;
    list.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveList = resolve;
        }),
    );
    evidence.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveDetail = resolve;
        }),
    );
    act(() => result.current.reload());
    expect(result.current.loading).toBe(true);
    expect(result.current.initialLoading).toBe(false);
    expect(result.current.items).toEqual(records);
    expect(result.current.detail).toBe(previous);
    expect(result.current.selectedId).toBe(detail.job.id);
    await act(async () => {
      resolveList?.(structuredClone(records));
      resolveDetail?.(structuredClone(detail));
    });
    await waitFor(() => expect(result.current.detailLoading).toBe(false));
    expect(result.current.detail).toBe(previous);
  });

  it('retains errors and cached data until a retry actually succeeds, then accepts changed measurements', async () => {
    const records = [
      { job: detail.job, summaries: detail.result?.summaries ?? null },
    ];
    const list = vi.spyOn(api, 'getHistory').mockResolvedValue(records);
    const evidence = vi
      .spyOn(api, 'getDetail')
      .mockResolvedValue(structuredClone(detail));
    const { result } = renderHook(() => useHistory(null));
    await waitFor(() => expect(result.current.detail).not.toBeNull());
    const previous = result.current.detail;
    list.mockRejectedValueOnce(new api.ApiError('History refresh unavailable'));
    evidence.mockRejectedValueOnce(
      new api.ApiError('Evidence refresh unavailable'),
    );
    act(() => result.current.reload());
    await waitFor(() =>
      expect(result.current.detailError).toBe('Evidence refresh unavailable'),
    );
    expect(result.current.detail).toBe(previous);
    expect(result.current.items).toEqual(records);
    let resolveDetail: ((value: HistoryDetail) => void) | undefined;
    evidence.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveDetail = resolve;
        }),
    );
    act(() => result.current.reload());
    expect(result.current.detailError).toBe('Evidence refresh unavailable');
    expect(result.current.detail).toBe(previous);
    const changed = structuredClone(detail);
    const summary = changed.result?.summaries[0];
    if (!summary) throw new Error('Expected fixture summary');
    summary.cpu_mean_core_percent = 123;
    await act(async () => {
      resolveDetail?.(changed);
    });
    await waitFor(() => expect(result.current.detailError).toBe(''));
    expect(
      result.current.detail?.result?.summaries[0]?.cpu_mean_core_percent,
    ).toBe(123);
    expect(result.current.detail).not.toBe(previous);
  });
  it('aborts an obsolete detail request and ignores its late response', async () => {
    const secondJob = job('COMPLETED', '00000000-0000-4000-8000-000000000002');
    vi.spyOn(api, 'getHistory').mockResolvedValue([
      { job: detail.job, summaries: null },
      { job: secondJob, summaries: null },
    ]);
    let resolveFirst: ((record: HistoryDetail) => void) | undefined;
    let firstSignal: AbortSignal | undefined;
    vi.spyOn(api, 'getDetail').mockImplementation((id, signal) => {
      if (id === detail.job.id) {
        firstSignal = signal;
        return new Promise((resolve) => {
          resolveFirst = resolve;
        });
      }
      return Promise.resolve({ ...detail, job: secondJob });
    });
    const { result } = renderHook(() => useHistory(null));
    await waitFor(() => expect(firstSignal).toBeDefined());
    act(() => result.current.select(secondJob.id));
    await waitFor(() =>
      expect(result.current.detail?.job.id).toBe(secondJob.id),
    );
    expect(firstSignal?.aborted).toBe(true);
    await act(async () => {
      resolveFirst?.(detail);
    });
    expect(result.current.detail?.job.id).toBe(secondJob.id);
  });

  it('keeps pagination user-owned after a local job has completed', async () => {
    const secondJob = job('COMPLETED', '00000000-0000-4000-8000-000000000002');
    const fetchHistory = vi
      .spyOn(api, 'getHistory')
      .mockImplementation((_signal, offset) =>
        Promise.resolve([
          { job: offset === 20 ? secondJob : detail.job, summaries: null },
        ]),
      );
    vi.spyOn(api, 'getDetail').mockImplementation((id) =>
      Promise.resolve({
        ...detail,
        job: id === secondJob.id ? secondJob : detail.job,
      }),
    );
    const { result } = renderHook(() => useHistory(detail.job.id));
    await waitFor(() =>
      expect(result.current.detail?.job.id).toBe(detail.job.id),
    );
    act(() => result.current.setOffset(20));
    await waitFor(() =>
      expect(result.current.detail?.job.id).toBe(secondJob.id),
    );
    expect(fetchHistory).toHaveBeenLastCalledWith(expect.any(AbortSignal), 20);
  });
});
