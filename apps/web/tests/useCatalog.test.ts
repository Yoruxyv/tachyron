import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import * as api from '@/api/benchmarks';
import type { Dataset } from '@/api/contracts';
import { useCatalog } from '@/features/datasets/hooks/useCatalog';

import { dataset } from './fixtures';

describe('dataset discovery ownership', () => {
  it('retains a confirmed import when an older catalog snapshot arrives', async () => {
    let finish: ((items: Dataset[]) => void) | undefined;
    vi.spyOn(api, 'getDatasets').mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const imported: Dataset = { ...dataset, dataset_id: 'import-confirmed' };
    const { result } = renderHook(useCatalog);
    act(() => result.current.acceptImported(imported));
    await act(async () => {
      finish?.([dataset]);
    });
    expect(result.current.datasets).toEqual([dataset, imported]);
    expect(result.current.error).toBe('');
  });
  it('loads only the catalog and aborts its request on unmount', async () => {
    const fetchCatalog = vi
      .spyOn(api, 'getDatasets')
      .mockResolvedValue([dataset]);
    const fetchHistory = vi.spyOn(api, 'getHistory');
    const { result, unmount } = renderHook(useCatalog);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.datasets).toEqual([dataset]);
    expect(result.current.error).toBe('');
    expect(result.current.verifiedAt).toBeGreaterThan(0);
    expect(fetchCatalog).toHaveBeenCalledTimes(1);
    expect(fetchHistory).not.toHaveBeenCalled();
    const signal = fetchCatalog.mock.calls[0]?.[0];
    expect(signal?.aborted).toBe(false);
    unmount();
    expect(signal?.aborted).toBe(true);
  });

  it.each([
    [
      new api.ApiError('Dataset registry unavailable'),
      'Dataset registry unavailable',
    ],
    [new Error('private server detail'), 'Unable to load saved experiments.'],
  ])(
    'retains cached data and sanitized errors until retry succeeds (%s)',
    async (failure, message) => {
      const fetchCatalog = vi
        .spyOn(api, 'getDatasets')
        .mockResolvedValue([dataset]);
      const { result } = renderHook(useCatalog);
      await waitFor(() => expect(result.current.loading).toBe(false));
      const verifiedAt = result.current.verifiedAt;
      fetchCatalog.mockRejectedValueOnce(failure);
      act(() => result.current.reload());
      await waitFor(() => expect(result.current.error).toBe(message));
      expect(result.current.datasets).toEqual([dataset]);
      expect(result.current.verifiedAt).toBe(verifiedAt);
      let complete: ((items: Dataset[]) => void) | undefined;
      fetchCatalog.mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            complete = resolve;
          }),
      );
      act(() => result.current.reload());
      expect(result.current.loading).toBe(true);
      expect(result.current.error).toBe(message);
      expect(result.current.datasets).toEqual([dataset]);
      await act(async () => {
        complete?.([]);
      });
      expect(result.current.loading).toBe(false);
      expect(result.current.datasets).toEqual([]);
      expect(result.current.error).toBe('');
      expect(result.current.verifiedAt).toBeGreaterThanOrEqual(verifiedAt);
    },
  );

  it('ignores an obsolete catalog response after a newer reload completes', async () => {
    let finishObsolete: ((items: Dataset[]) => void) | undefined;
    const fetchCatalog = vi
      .spyOn(api, 'getDatasets')
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishObsolete = resolve;
          }),
      )
      .mockResolvedValueOnce([dataset]);
    const { result } = renderHook(useCatalog);
    const obsoleteSignal = fetchCatalog.mock.calls[0]?.[0];
    act(() => result.current.reload());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(obsoleteSignal?.aborted).toBe(true);
    expect(result.current.datasets).toEqual([dataset]);
    await act(async () => {
      finishObsolete?.([]);
    });
    expect(result.current.datasets).toEqual([dataset]);
    expect(fetchCatalog).toHaveBeenCalledTimes(2);
  });
});
