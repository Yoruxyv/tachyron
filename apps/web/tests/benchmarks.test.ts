import { describe, expect, it, vi } from 'vitest';

import {
  ApiError,
  getDatasets,
  getDetail,
  getJob,
  startJob,
} from '@/api/benchmarks';
import { parseJob, parseMetrics } from '@/api/decode';

import { dataset, detail, job, metrics, request } from './fixtures';

describe('benchmark API boundary', () => {
  it('reads typed datasets and preserves the server-owned workload metadata', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>().mockResolvedValue(Response.json([dataset])),
    );
    await expect(getDatasets(new AbortController().signal)).resolves.toEqual([
      dataset,
    ]);
  });

  it('submits the exact backend contract and a stable idempotency key', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(Response.json(job(), { status: 202 }));
    vi.stubGlobal('fetch', fetchMock);
    await expect(
      startJob(request, 'key-1', new AbortController().signal),
    ).resolves.toEqual(job());
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/jobs',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify(request),
        headers: expect.objectContaining({ 'Idempotency-Key': 'key-1' }),
      }),
    );
  });

  it.each([404, 409, 422, 429, 503, 500])(
    'sanitizes HTTP %i and retains bounded retry timing',
    async (status) => {
      vi.stubGlobal(
        'fetch',
        vi.fn<typeof fetch>().mockResolvedValue(
          new Response('private traceback and secret path', {
            status,
            headers: { 'Retry-After': '3' },
          }),
        ),
      );
      await expect(
        getJob(job().id, new AbortController().signal),
      ).rejects.toMatchObject({ status, retryAfter: 3000 });
      await expect(
        getJob(job().id, new AbortController().signal),
      ).rejects.not.toThrow('private');
    },
  );

  it('passes unmount cancellation and bounds requests to ten seconds', async () => {
    const timeout = vi.spyOn(AbortSignal, 'timeout');
    const controller = new AbortController();
    controller.abort();
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>().mockImplementation((_url, options) => {
        expect(options?.signal?.aborted).toBe(true);
        return Promise.reject(new DOMException('Aborted', 'AbortError'));
      }),
    );
    await expect(getDatasets(controller.signal)).rejects.toThrow('Aborted');
    expect(timeout).toHaveBeenCalledWith(10000);
  });

  it('rejects invalid response shapes instead of fabricating metrics', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>().mockResolvedValue(Response.json({ job: null })),
    );
    await expect(
      getDetail(job().id, new AbortController().signal),
    ).rejects.toBeInstanceOf(ApiError);
    expect(() => parseJob({ ...job(), state: 'SUCCESS' })).toThrow();
    expect(() => parseJob({ ...job(), completed_batches: 25 })).toThrow();
    expect(() => parseMetrics({ ...metrics, summaries: [] })).toThrow();
    expect(() =>
      parseMetrics({
        ...metrics,
        summaries: [metrics.summaries[0], ...metrics.summaries.slice(0, 3)],
      }),
    ).toThrow();
    expect(() => parseMetrics({ ...metrics, schema_version: 2 })).toThrow();
  });

  it('preserves explicit missing observations and accepts CPU above one core', () => {
    const parsed = parseMetrics({
      ...metrics,
      summaries: metrics.summaries.map((summary) => ({
        ...summary,
        cpu_mean_core_percent: null,
        rss_peak_mib: null,
      })),
    });
    expect(parsed.summaries[0]?.cpu_mean_core_percent).toBeNull();
    expect(parseMetrics(metrics).summaries[3]?.cpu_mean_core_percent).toBe(560);
  });

  it('reads saved evidence without changing its comparison context', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>().mockResolvedValue(Response.json(detail)),
    );
    await expect(
      getDetail(job().id, new AbortController().signal),
    ).resolves.toEqual(detail);
  });
});
