import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import App from '@/App';
import * as api from '@/api/benchmarks';
import JobForm from '@/features/benchmarks/JobForm';
import JobProgress from '@/features/benchmarks/JobProgress';
import Results from '@/features/benchmarks/Results';
import ApiStatus from '@/features/benchmarks/ApiStatus';

import { dataset, detail, job } from './fixtures';

vi.mock('@/features/benchmarks/Charts', () => ({
  default: () => <p>Charts loaded</p>,
}));

beforeEach(() => {
  sessionStorage.clear();
  window.history.replaceState(null, '', '/');
  vi.spyOn(api, 'getDatasets').mockResolvedValue([dataset]);
  vi.spyOn(api, 'getHistory').mockResolvedValue([]);
});

describe('dashboard behavior', () => {
  it('retains saved evidence and worker inspection across view changes and focuses history search', async () => {
    const user = userEvent.setup();
    vi.mocked(api.getHistory).mockResolvedValue([
      { job: detail.job, summaries: detail.result?.summaries ?? null },
    ]);
    vi.spyOn(api, 'getDetail').mockResolvedValue(detail);
    render(<App />);
    await user.click(
      await screen.findByRole('button', { name: /test-images/ }),
    );
    expect(
      await screen.findByRole('heading', {
        level: 1,
        name: 'Scaling comparison',
      }),
    ).toBeVisible();
    expect(
      await screen.findByRole('link', { name: /Open raw API evidence/ }),
    ).toHaveAttribute('href', `/api/benchmarks/${detail.job.id}`);
    await user.click(screen.getByRole('button', { name: /^8\s*workers$/ }));
    await user.click(screen.getByRole('link', { name: 'History' }));
    await waitFor(() =>
      expect(
        screen.getByRole('searchbox', { name: 'Search runs' }),
      ).toHaveFocus(),
    );
    expect(
      screen.getByRole('combobox', { name: 'Saved comparison' }),
    ).toHaveValue(detail.job.id);
    expect(
      screen.getByRole('button', { name: /^8\s*workers$/ }),
    ).toHaveAttribute('aria-pressed', 'true');
    await user.click(screen.getByRole('link', { name: 'Overview' }));
    expect(
      screen.getByRole('button', { name: /^8\s*workers$/ }),
    ).toHaveAttribute('aria-pressed', 'true');
  });

  it('keeps dataset and history retries independent and exposes the empty registry', async () => {
    const user = userEvent.setup();
    vi.mocked(api.getDatasets).mockRejectedValueOnce(
      new api.ApiError('Datasets unavailable', 503),
    );
    vi.mocked(api.getHistory).mockRejectedValueOnce(
      new api.ApiError('History unavailable', 503),
    );
    render(<App />);
    expect(await screen.findByText('Datasets unavailable')).toHaveAttribute(
      'role',
      'alert',
    );
    expect(await screen.findByText('History unavailable')).toHaveAttribute(
      'role',
      'alert',
    );
    vi.mocked(api.getDatasets).mockResolvedValue([]);
    await user.click(screen.getByRole('button', { name: 'Retry datasets' }));
    expect(await screen.findByText(/No datasets registered/)).toBeVisible();
    expect(screen.getByText('History unavailable')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Retry history' }));
    await waitFor(() =>
      expect(screen.queryByText('History unavailable')).not.toBeInTheDocument(),
    );
    expect(
      screen.getByRole('button', { name: /Run experiment/ }),
    ).toBeDisabled();
  });

  it('recovers an active job without restarting polling on navigation and preserves cancellation announcements', async () => {
    const user = userEvent.setup();
    sessionStorage.setItem(
      'tachyron.active.v1',
      JSON.stringify({ id: job().id }),
    );
    const getJob = vi.spyOn(api, 'getJob').mockResolvedValue(job('RUNNING'));
    const startJob = vi.spyOn(api, 'startJob');
    vi.spyOn(api, 'cancelJob').mockResolvedValue(job('CANCELLED'));
    render(<App />);
    expect(
      await screen.findByRole('link', { name: 'View job progress' }),
    ).toBeVisible();
    const pollingSignal = getJob.mock.calls[0]?.[1];
    expect(pollingSignal).toBeDefined();
    await user.click(screen.getByRole('link', { name: /^Experiments/ }));
    expect(pollingSignal?.aborted).toBe(false);
    await user.click(screen.getByRole('button', { name: 'Cancel experiment' }));
    expect(await screen.findByText(/CANCELLED ·/)).toBeVisible();
    expect(
      screen.queryByRole('link', { name: 'View job progress' }),
    ).not.toBeInTheDocument();
    expect(startJob).not.toHaveBeenCalled();
    expect(pollingSignal?.aborted).toBe(true);
  });

  it('shows honest empty metrics and registered controls with no synthetic results', async () => {
    render(<App />);
    expect(
      screen.getByRole('heading', { level: 1, name: 'Overview' }),
    ).toBeVisible();
    expect(await screen.findByText(/No benchmark runs yet/)).toBeVisible();
    expect(screen.getAllByText('Unavailable')).toHaveLength(4);
    expect(screen.getByRole('combobox', { name: 'Dataset' })).toHaveValue(
      dataset.dataset_id,
    );
    expect(screen.getByText('1 · 2 · 4 · 8 workers')).toBeVisible();
  });

  it('validates controls before submission and does not invent a worker request field', async () => {
    const user = userEvent.setup();
    const submit = vi
      .fn<(request: Parameters<typeof api.startJob>[0]) => Promise<void>>()
      .mockResolvedValue(undefined);
    render(
      <JobForm
        datasets={[dataset]}
        disabled={false}
        pending={undefined}
        submit={submit}
      />,
    );
    await user.clear(screen.getByLabelText('Width (px)'));
    await user.type(screen.getByLabelText('Width (px)'), '4097');
    await user.click(screen.getByRole('button', { name: /Run experiment/ }));
    expect(screen.getByRole('alert')).toHaveTextContent(
      'dimensions from 1 to 4096',
    );
    expect(submit).not.toHaveBeenCalled();
    await user.clear(screen.getByLabelText('Width (px)'));
    await user.type(screen.getByLabelText('Width (px)'), '4');
    await user.click(screen.getByRole('button', { name: /Run experiment/ }));
    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({ width: 4, dataset_id: dataset.dataset_id }),
    );
    expect(submit.mock.calls[0]?.[0]).not.toHaveProperty('workers');
  });

  it('disables unavailable datasets and distinguishes failed job evidence', async () => {
    render(
      <JobForm
        datasets={[
          { ...dataset, error_code: 'dataset_unavailable', input_count: null },
        ]}
        disabled={false}
        pending={undefined}
        submit={vi.fn()}
      />,
    );
    expect(
      screen.getByRole('button', { name: /Run experiment/ }),
    ).toBeDisabled();
    expect(screen.getByRole('option', { name: /unavailable/ })).toBeDisabled();
  });

  it('supports keyboard navigation, focus transfer, and real job acceptance', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'startJob').mockResolvedValue(job());
    vi.spyOn(api, 'getJob').mockResolvedValue(job('RUNNING'));
    render(<App />);
    await user.tab();
    expect(screen.getByRole('link', { name: 'Skip to content' })).toHaveFocus();
    await user.click(screen.getByRole('link', { name: 'Experiments' }));
    await screen.findByRole('heading', { level: 1, name: 'Experiments' });
    await waitFor(() => expect(screen.getByRole('main')).toHaveFocus());
    expect(window.scrollTo).toHaveBeenCalledWith(0, 0);
    await user.click(screen.getByRole('link', { name: 'Skip to content' }));
    expect(
      screen.getByRole('heading', { level: 1, name: 'Experiments' }),
    ).toBeVisible();
    expect(screen.getByRole('main')).toHaveFocus();
    await user.click(screen.getByRole('button', { name: /Run experiment/ }));
    expect(await screen.findByText(/RUNNING · 0 \/ 24/)).toBeVisible();
    expect(screen.getByRole('progressbar')).toHaveAttribute('value', '0');
    expect(
      screen.getByRole('button', { name: /Run experiment/ }),
    ).toBeDisabled();
    expect(
      screen.getByRole('button', { name: 'Cancel experiment' }),
    ).toBeEnabled();
  });

  it('shows complete persisted results with units, spread and theoretical baseline labels', async () => {
    render(<Results detail={detail} selected={4} select={vi.fn()} />);
    expect(screen.getByRole('table')).toBeVisible();
    expect(screen.getByText('CPU (% of 1 core)')).toBeVisible();
    expect(screen.getByText('Peak RSS (MiB)')).toBeVisible();
    expect(screen.getByText('Time mean ± SD (s)')).toBeVisible();
    expect(screen.getByText('Speedup / ideal')).toBeVisible();
    expect(await screen.findByText('Charts loaded')).toBeVisible();
  });

  it('keeps incomplete evidence unavailable and announces terminal failures', () => {
    render(
      <>
        <Results
          detail={{ ...detail, job: job('FAILED') }}
          selected={4}
          select={vi.fn()}
        />
        <JobProgress job={job('FAILED')} busy={false} cancel={vi.fn()} />
      </>,
    );
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent(
      'The experiment failed',
    );
    expect(
      screen.queryByRole('button', { name: 'Cancel experiment' }),
    ).not.toBeInTheDocument();
  });

  it('keeps the overview compact while exposing its measured table by keyboard-accessible disclosure', async () => {
    const user = userEvent.setup();
    render(<Results detail={detail} selected={4} select={vi.fn()} overview />);
    expect(screen.getByText('Measured results')).not.toBeVisible();
    await user.click(screen.getByText('Measured values and sample spread'));
    expect(screen.getByRole('table')).toBeVisible();
  });

  it('keeps health errors safe, permits retries, and aborts on unmount', async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response('secret traceback', { status: 500 }))
      .mockResolvedValueOnce(Response.json({ status: 'ok' }));
    vi.stubGlobal('fetch', fetchMock);
    const { unmount } = render(<ApiStatus />);
    await user.click(screen.getByRole('button', { name: 'Check connection' }));
    expect(await screen.findByText(/API unavailable/)).toBeVisible();
    expect(screen.queryByText(/secret traceback/)).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Check connection' }));
    expect(await screen.findByText('API online')).toBeVisible();
    let signal: AbortSignal | null | undefined;
    fetchMock.mockImplementation((_url, options) => {
      signal = options?.signal;
      return new Promise(() => {});
    });
    await user.click(screen.getByRole('button', { name: 'Check connection' }));
    unmount();
    expect(signal?.aborted).toBe(true);
  });
});
