import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/api/benchmarks';
import * as api from '@/api/operations';
import { useOperationalExecution } from '@/features/operations/useOperationalExecution';

import { operation, runId } from './operation-fixtures';

const manual = {
  dataset_id: 'browser-test',
  width: 16,
  height: 12,
  mode: 'manual',
  workers: 2,
} as const;
const accepted = api.parseOperation(operation(), runId, null);

beforeEach(() => sessionStorage.clear());

describe('operational submission ownership', () => {
  it('issues exactly one request for repeated starts before React renders', async () => {
    const post = vi.spyOn(api, 'startOperation').mockResolvedValue(accepted);
    const { result, unmount } = renderHook(useOperationalExecution);
    await act(async () => {
      const start = result.current.start;
      await Promise.all([start(manual), start(manual)]);
      await start(manual);
    });
    expect(post).toHaveBeenCalledTimes(1);
    expect(result.current.runId).toBe(runId);
    expect(result.current.running).toBe(true);
    unmount();

    const recovered = renderHook(useOperationalExecution);
    expect(recovered.result.current.runId).toBe(runId);
    expect(post).toHaveBeenCalledTimes(1);
    act(() => recovered.result.current.observe(runId, 'COMPLETED'));
    expect(recovered.result.current.running).toBe(false);
  });

  it.each([400, 409, 422, 429])(
    'reports definitive HTTP %i without creating uncertainty',
    async (status) => {
      const post = vi
        .spyOn(api, 'startOperation')
        .mockRejectedValue(new ApiError('Sanitized', status));
      const { result } = renderHook(useOperationalExecution);
      await act(async () => result.current.start(manual));
      expect(result.current.error).toBeTruthy();
      expect(result.current.uncertain).toBeFalsy();
      expect(result.current.runId).toBeNull();
      expect(post).toHaveBeenCalledTimes(1);
    },
  );

  it.each([0, 503])(
    'requires an explicit reset after an ambiguous HTTP result (%i)',
    async (status) => {
      const post = vi
        .spyOn(api, 'startOperation')
        .mockRejectedValue(new ApiError('Lost response', status));
      const first = renderHook(useOperationalExecution);
      await act(async () => first.result.current.start(manual));
      expect(first.result.current.uncertain).toBe(true);
      first.unmount();

      const { result } = renderHook(useOperationalExecution);
      expect(result.current.uncertain).toBe(true);
      await act(async () => result.current.start(manual));
      expect(post).toHaveBeenCalledTimes(1);
      act(() => result.current.clearUncertainty());
      expect(result.current.uncertain).toBeFalsy();
      expect(post).toHaveBeenCalledTimes(1);
    },
  );

  it('preserves uncertainty when the page unloads while submission is pending', async () => {
    const post = vi
      .spyOn(api, 'startOperation')
      .mockImplementation(() => new Promise(() => {}));
    const first = renderHook(useOperationalExecution);
    act(() => {
      void first.result.current.start(manual);
    });
    expect(first.result.current.sending).toBe(true);
    first.unmount();
    expect(post.mock.calls[0]?.[1].aborted).toBe(true);
    const { result } = renderHook(useOperationalExecution);
    expect(result.current.uncertain).toBe(true);
    expect(post).toHaveBeenCalledTimes(1);
  });
});
