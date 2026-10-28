import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import * as api from '@/api/benchmarks';
import type { HistoryDetail } from '@/api/contracts';
import { useHistory } from '@/features/benchmarks/useHistory';

import { detail, job } from './fixtures';

describe('history selection races', () => {
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
