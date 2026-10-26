import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import App from '../src/App';

describe('application shell', () => {
  it('shows an honest empty state and a keyboard-accessible health check', async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>().mockResolvedValue(Response.json({ status: 'ok' })),
    );
    render(<App />);

    expect(
      screen.getByRole('heading', { level: 1, name: 'Tachyron' }),
    ).toBeVisible();
    expect(
      screen.getByRole('heading', { name: 'No benchmark runs yet' }),
    ).toBeVisible();
    expect(screen.getByRole('status')).toHaveTextContent('Not checked');
    await user.tab();
    expect(
      screen.getByRole('button', { name: 'Check connection' }),
    ).toHaveFocus();
    await user.keyboard('{Enter}');

    expect(await screen.findByText('API online')).toBeVisible();
  });

  it('shows a safe failure message and lets the user retry', async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response('secret server traceback', { status: 500 }),
      )
      .mockResolvedValueOnce(Response.json({ status: 'ok' }));
    vi.stubGlobal('fetch', fetchMock);
    render(<App />);

    await user.click(screen.getByRole('button', { name: 'Check connection' }));
    expect(await screen.findByText(/API unavailable/)).toBeVisible();
    expect(
      screen.queryByText(/secret server traceback/),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Check connection' }));

    expect(await screen.findByText('API online')).toBeVisible();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('prevents overlapping requests and aborts on unmount', async () => {
    const user = userEvent.setup();
    let signal: AbortSignal | null | undefined;
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockImplementation((_url, options) => {
        signal = options?.signal;
        return new Promise<Response>(() => {});
      });
    vi.stubGlobal('fetch', fetchMock);
    const { unmount } = render(<App />);

    await user.click(screen.getByRole('button', { name: 'Check connection' }));
    expect(screen.getByRole('button', { name: 'Checking…' })).toBeDisabled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    unmount();

    expect(signal?.aborted).toBe(true);
  });
});
