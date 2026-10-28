import { StrictMode, useEffect } from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';

import * as api from '@/api/health';
import { useApiHealth } from '@/features/benchmarks/useApiHealth';

function useCheckedHealth() {
  const health = useApiHealth();
  const check = health.check;
  useEffect(() => {
    void check();
  }, [check]);
  return health;
}

it('restarts the StrictMode liveness probe and keeps the new request owned until cleanup', async () => {
  const signals: AbortSignal[] = [];
  const check = vi.spyOn(api, 'getHealth').mockImplementation(
    (signal) =>
      new Promise((_resolve, reject) => {
        signals.push(signal);
        signal.addEventListener(
          'abort',
          () => reject(new DOMException('Aborted', 'AbortError')),
          { once: true },
        );
      }),
  );
  const { result, unmount } = renderHook(useCheckedHealth, {
    wrapper: StrictMode,
  });
  await waitFor(() => expect(check).toHaveBeenCalledTimes(2));
  expect(signals[0]?.aborted).toBe(true);
  expect(signals[1]?.aborted).toBe(false);
  await act(async () => {
    await result.current.check();
  });
  expect(check).toHaveBeenCalledTimes(2);
  expect(result.current.checking).toBe(true);
  unmount();
  expect(signals.every((signal) => signal.aborted)).toBe(true);
});
