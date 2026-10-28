import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '@/api/benchmarks';
import { useJob } from '@/features/benchmarks/hooks/useJob';

import { job, request } from './fixtures';

beforeEach(() => {
  sessionStorage.clear();
  vi.useFakeTimers();
});

describe('job lifecycle ownership', () => {
  it('keeps a cached running job unverified while its failed status request is retried', async () => {
    vi.advanceTimersByTime(5);
    sessionStorage.setItem(
      'tachyron.active.v1',
      JSON.stringify({ id: job().id }),
    );
    vi.spyOn(api, 'getJob')
      .mockResolvedValueOnce(job('RUNNING'))
      .mockRejectedValueOnce(new api.ApiError('Status unavailable'))
      .mockImplementation(() => new Promise(() => {}));
    const { result, unmount } = renderHook(useJob);
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.job?.state).toBe('RUNNING');
    expect(result.current.verifiedAt).toBeGreaterThan(0);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(result.current.error).toBe('Status unavailable');
    expect(result.current.verifiedAt).toBe(0);
    act(() => result.current.resume());
    expect(result.current.error).toBe('');
    expect(result.current.job?.state).toBe('RUNNING');
    expect(result.current.verifiedAt).toBe(0);
    expect(
      JSON.parse(sessionStorage.getItem('tachyron.active.v1') ?? '{}'),
    ).toEqual({ id: job().id });
    unmount();
  });
  it('distinguishes acceptance, progress, and terminal completion and stops polling', async () => {
    vi.spyOn(api, 'startJob').mockResolvedValue(job());
    const poll = vi
      .spyOn(api, 'getJob')
      .mockResolvedValueOnce({ ...job('RUNNING'), completed_batches: 2 })
      .mockResolvedValue(job('COMPLETED'));
    const { result } = renderHook(useJob);
    await act(async () => {
      await result.current.submit(request);
    });
    expect(result.current.job?.state).toBe('RUNNING');
    expect(result.current.job?.completed_batches).toBe(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(result.current.job?.state).toBe('COMPLETED');
    expect(result.current.polling).toBe(false);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(poll).toHaveBeenCalledTimes(2);
  });

  it('prevents duplicate submissions and retries ambiguous acceptance with the same key', async () => {
    const submit = vi
      .spyOn(api, 'startJob')
      .mockRejectedValueOnce(new api.ApiError('Network timeout'))
      .mockResolvedValue(job());
    vi.spyOn(api, 'getJob').mockResolvedValue(job('RUNNING'));
    const { result } = renderHook(useJob);
    await act(async () => {
      await Promise.all([
        result.current.submit(request),
        result.current.submit(request),
      ]);
    });
    expect(submit).toHaveBeenCalledTimes(1);
    const firstKey = submit.mock.calls[0]?.[1];
    expect(result.current.pending?.key).toBe(firstKey);
    await act(async () => {
      await result.current.submit(request);
    });
    expect(submit.mock.calls[1]?.[1]).toBe(firstKey);
    expect(result.current.pending).toBeNull();
    await act(async () => {
      await result.current.submit(request);
    });
    expect(submit).toHaveBeenCalledTimes(2);
  });

  it('respects Retry-After for queue rejection', async () => {
    const submit = vi
      .spyOn(api, 'startJob')
      .mockRejectedValue(new api.ApiError('Queue full', 429, 3000));
    const { result } = renderHook(useJob);
    await act(async () => {
      await result.current.submit(request);
    });
    await act(async () => {
      await result.current.submit(request);
    });
    expect(submit).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
      await result.current.submit(request);
    });
    expect(submit).toHaveBeenCalledTimes(2);
    expect(result.current.pending).toBeNull();
  });

  it('keeps submission identity after a server/proxy failure whose acceptance is unknown', async () => {
    const submit = vi
      .spyOn(api, 'startJob')
      .mockRejectedValueOnce(new api.ApiError('Service unavailable', 503, 3000))
      .mockResolvedValue(job('COMPLETED'));
    const { result } = renderHook(useJob);
    await act(async () => {
      await result.current.submit(request);
    });
    const key = result.current.pending?.key;
    expect(result.current.coolingDown).toBe(true);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
      await result.current.submit(request);
    });
    expect(submit.mock.calls[1]?.[1]).toBe(key);
    expect(result.current.job?.state).toBe('COMPLETED');
  });

  it('retains an ambiguous submission across reload and aborts in-flight polling on unmount', async () => {
    vi.spyOn(api, 'startJob').mockRejectedValue(
      new api.ApiError('Lost acceptance'),
    );
    const first = renderHook(useJob);
    await act(async () => {
      await first.result.current.submit(request);
    });
    const key = first.result.current.pending?.key;
    first.unmount();
    const second = renderHook(useJob);
    expect(second.result.current.pending?.key).toBe(key);
    second.unmount();

    sessionStorage.setItem(
      'tachyron.active.v1',
      JSON.stringify({ id: job().id }),
    );
    let signal: AbortSignal | undefined;
    vi.spyOn(api, 'getJob').mockImplementation((_id, requestSignal) => {
      signal = requestSignal;
      return new Promise(() => {});
    });
    const restored = renderHook(useJob);
    expect(restored.result.current.recovering).toBe(true);
    restored.unmount();
    expect(signal?.aborted).toBe(true);
  });

  it('shows failed and cancelled terminal states without promoting metrics', async () => {
    vi.spyOn(api, 'startJob').mockResolvedValue(job());
    vi.spyOn(api, 'getJob').mockResolvedValue(job('FAILED'));
    const { result } = renderHook(useJob);
    await act(async () => {
      await result.current.submit(request);
    });
    expect(result.current.job?.state).toBe('FAILED');
    expect(result.current.polling).toBe(false);
  });

  it('shows cancellation as requested until a terminal server acknowledgement', async () => {
    vi.spyOn(api, 'startJob').mockResolvedValue(job());
    vi.spyOn(api, 'getJob').mockResolvedValue(job('RUNNING'));
    vi.spyOn(api, 'cancelJob').mockResolvedValue({
      ...job('RUNNING'),
      cancel_requested: true,
    });
    const { result } = renderHook(useJob);
    await act(async () => {
      await result.current.submit(request);
    });
    await act(async () => {
      await result.current.cancel();
    });
    expect(result.current.job?.state).toBe('RUNNING');
    expect(result.current.job?.cancel_requested).toBe(true);
    expect(result.current.polling).toBe(true);
  });

  it('does not let an older polling response revoke acknowledged cancellation', async () => {
    vi.spyOn(api, 'startJob').mockResolvedValue(job());
    let resolvePoll: ((snapshot: ReturnType<typeof job>) => void) | undefined;
    vi.spyOn(api, 'getJob').mockImplementation(
      () =>
        new Promise((resolve) => {
          resolvePoll = resolve;
        }),
    );
    vi.spyOn(api, 'cancelJob').mockResolvedValue({
      ...job('RUNNING'),
      cancel_requested: true,
    });
    const { result } = renderHook(useJob);
    await act(async () => {
      await result.current.submit(request);
    });
    await act(async () => {
      await result.current.cancel();
    });
    await act(async () => {
      resolvePoll?.(job('RUNNING'));
    });
    expect(result.current.job?.cancel_requested).toBe(true);
  });

  it('pauses after repeated failures and resumes without resubmitting a job', async () => {
    sessionStorage.setItem(
      'tachyron.active.v1',
      JSON.stringify({ id: job().id }),
    );
    const poll = vi
      .spyOn(api, 'getJob')
      .mockRejectedValue(new api.ApiError('Offline'));
    const submit = vi.spyOn(api, 'startJob');
    const { result } = renderHook(useJob);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60000);
    });
    expect(result.current.polling).toBe(false);
    expect(poll).toHaveBeenCalledTimes(5);
    poll.mockResolvedValue(job('COMPLETED'));
    await act(async () => {
      result.current.resume();
    });
    expect(result.current.job?.state).toBe('COMPLETED');
    expect(submit).not.toHaveBeenCalled();
  });

  it('releases a missing recovered job and reports unavailable storage', async () => {
    sessionStorage.setItem(
      'tachyron.active.v1',
      JSON.stringify({ id: job().id }),
    );
    vi.spyOn(api, 'getJob').mockRejectedValue(new api.ApiError('Missing', 404));
    vi.spyOn(api, 'startJob').mockResolvedValue(job('COMPLETED'));
    const { result } = renderHook(useJob);
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.recovering).toBe(false);
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('Storage denied');
    });
    await act(async () => {
      await result.current.submit(request);
    });
    expect(result.current.storageWarning).toContain(
      'Session recovery is unavailable',
    );
    expect(result.current.job?.state).toBe('COMPLETED');
  });
});
