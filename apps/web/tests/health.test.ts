import { describe, expect, it, vi } from 'vitest';

import { getHealth } from '../src/api/health';

describe('getHealth', () => {
  it('checks the same-origin health contract', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(Response.json({ status: 'ok' }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(getHealth(new AbortController().signal)).resolves.toEqual({
      status: 'ok',
    });
    expect(fetchMock).toHaveBeenCalledWith(
      '/health',
      expect.objectContaining({
        signal: expect.any(AbortSignal),
      }),
    );
  });

  it.each([
    [
      'HTTP failure',
      () => new Response('private server error', { status: 503 }),
    ],
    ['unexpected status', () => Response.json({ status: 'failed' })],
    ['missing status', () => Response.json({})],
    ['null response', () => Response.json(null)],
    ['invalid JSON', () => new Response('<html>proxy error</html>')],
  ])('rejects %s', async (_label, response) => {
    vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockResolvedValue(response()));

    await expect(getHealth(new AbortController().signal)).rejects.toThrow();
  });

  it('propagates transport failures', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>().mockRejectedValue(new TypeError('offline')),
    );

    await expect(getHealth(new AbortController().signal)).rejects.toThrow(
      'offline',
    );
  });

  it('passes caller cancellation to the request', async () => {
    const controller = new AbortController();
    controller.abort();
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockImplementation((_url, options) => {
        expect(options?.signal?.aborted).toBe(true);
        return Promise.reject(new DOMException('Aborted', 'AbortError'));
      });
    vi.stubGlobal('fetch', fetchMock);

    await expect(getHealth(controller.signal)).rejects.toThrow('Aborted');
  });

  it('bounds the request to five seconds', async () => {
    const timeout = new AbortController();
    const timeoutMock = vi
      .spyOn(AbortSignal, 'timeout')
      .mockReturnValue(timeout.signal);
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>().mockImplementation(
        (_url, options) =>
          new Promise<Response>((_resolve, reject) => {
            options?.signal?.addEventListener(
              'abort',
              () => {
                reject(
                  new DOMException('Health check timed out', 'TimeoutError'),
                );
              },
              { once: true },
            );
          }),
      ),
    );

    const request = getHealth(new AbortController().signal);
    timeout.abort();

    await expect(request).rejects.toThrow('Health check timed out');
    expect(timeoutMock).toHaveBeenCalledWith(5000);
  });
});
