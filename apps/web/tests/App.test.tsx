import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import App from '@/App';
import * as api from '@/api/benchmarks';
import * as healthApi from '@/api/health';
import JobForm from '@/features/benchmarks/experiments/JobForm';
import JobProgress from '@/features/benchmarks/experiments/JobProgress';
import Results from '@/features/benchmarks/results/Results';
import ApiStatus from '@/features/diagnostics/ApiStatus';
import { useApiHealth } from '@/features/diagnostics/hooks/useApiHealth';

import { dataset, detail, job, request } from './fixtures';

vi.mock('@/features/benchmarks/results/Charts', () => ({
  default: () => <p>Charts loaded</p>,
}));

beforeEach(() => {
  sessionStorage.clear();
  window.history.replaceState(null, '', '/');
  vi.spyOn(api, 'getDatasets').mockResolvedValue([dataset]);
  vi.spyOn(api, 'getHistory').mockResolvedValue([]);
  vi.spyOn(healthApi, 'getHealth').mockResolvedValue({ status: 'ok' });
});

function ApiStatusHarness() {
  const health = useApiHealth();
  return <ApiStatus health={health} />;
}

describe('dashboard behavior', () => {
  it('consolidates identical history and evidence errors while retaining separate failures', async () => {
    const user = userEvent.setup();
    window.history.replaceState(null, '', '/#history');
    vi.mocked(api.getHistory).mockResolvedValue([
      { job: detail.job, summaries: detail.result?.summaries ?? null },
    ]);
    vi.spyOn(api, 'getDetail').mockResolvedValue(detail);
    render(<App />);
    await waitFor(() =>
      expect(
        screen.getByRole('combobox', { name: 'Saved comparison' }),
      ).toHaveValue(detail.job.id),
    );
    vi.mocked(api.getHistory).mockRejectedValue(
      new api.ApiError('History connection unavailable'),
    );
    vi.mocked(api.getDetail).mockRejectedValue(
      new api.ApiError('History connection unavailable'),
    );
    await user.click(screen.getByRole('button', { name: 'Refresh history' }));
    expect(
      await screen.findAllByText('History connection unavailable'),
    ).toHaveLength(1);
    expect(screen.getByRole('button', { name: 'Retry history' })).toBeVisible();
    expect(
      screen.queryByRole('button', { name: 'Retry evidence' }),
    ).not.toBeInTheDocument();
    vi.mocked(api.getDetail).mockRejectedValue(
      new api.ApiError('Evidence record unavailable'),
    );
    await user.click(screen.getByRole('button', { name: 'Retry history' }));
    expect(
      await screen.findByText('Evidence record unavailable'),
    ).toBeVisible();
    expect(
      screen.getByRole('button', { name: 'Retry evidence' }),
    ).toBeVisible();
    expect(screen.getByText('History connection unavailable')).toBeVisible();
  });
  it.each([false, true])(
    'returns focus to the top and honors reduced motion (%s)',
    async (reduced) => {
      vi.stubGlobal(
        'matchMedia',
        vi.fn(() => ({ matches: reduced })),
      );
      const user = userEvent.setup();
      render(<App />);
      await user.click(screen.getByRole('button', { name: 'Back to top ↑' }));
      expect(window.scrollTo).toHaveBeenLastCalledWith({
        top: 0,
        behavior: reduced ? 'auto' : 'smooth',
      });
      expect(screen.getByRole('main')).toHaveFocus();
    },
  );
  it('uses shared liveness, registry and recovered job state in the compact sidebar', async () => {
    const user = userEvent.setup();
    sessionStorage.setItem(
      'tachyron.active.v1',
      JSON.stringify({ id: job().id }),
    );
    vi.spyOn(api, 'getJob').mockResolvedValue(job('RUNNING'));
    render(<App />);
    const sidebar = screen.getByRole('complementary');
    expect(await within(sidebar).findByText('API online')).toBeVisible();
    expect(
      within(sidebar).queryByText('Liveness only'),
    ).not.toBeInTheDocument();
    expect(
      within(sidebar).queryByText(/registered dataset/),
    ).not.toBeInTheDocument();
    expect(healthApi.getHealth).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole('link', { name: 'API Tests' }));
    expect(
      within(screen.getByRole('main')).getByText('API online'),
    ).toBeVisible();
    expect(healthApi.getHealth).toHaveBeenCalledTimes(1);
    vi.mocked(healthApi.getHealth).mockRejectedValueOnce(
      new Error('private connection details'),
    );
    await user.click(screen.getByRole('button', { name: 'Check connection' }));
    expect(await within(sidebar).findByText('API offline')).toBeVisible();
    expect(
      within(sidebar).queryByText(/registered dataset/),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText('private connection details'),
    ).not.toBeInTheDocument();
  });

  it('distinguishes an unavailable registry from an empty registered dataset list', async () => {
    const user = userEvent.setup();
    vi.mocked(api.getDatasets).mockRejectedValueOnce(
      new api.ApiError('Registry unavailable', 503),
    );
    render(<App />);
    await screen.findByText('Registry unavailable');
    expect(
      within(screen.getByRole('complementary')).queryByText(
        /registered dataset/,
      ),
    ).not.toBeInTheDocument();

    vi.mocked(api.getDatasets).mockResolvedValue([]);
    await user.click(screen.getByRole('button', { name: 'Retry datasets' }));
    expect(await screen.findByText(/No datasets registered/)).toBeVisible();
  });
  it('shares registered dataset selection between header, quick form and experiment navigation', async () => {
    const user = userEvent.setup();
    const second = { ...dataset, dataset_id: 'second-images' };
    vi.mocked(api.getDatasets).mockResolvedValue([dataset, second]);
    render(<App />);
    const header = await screen.findByRole('combobox', {
      name: 'Select benchmark dataset',
    });
    await waitFor(() => expect(header).toHaveValue(dataset.dataset_id));
    await user.click(header);
    await user.click(screen.getByRole('option', { name: second.dataset_id }));
    expect(screen.getByRole('combobox', { name: 'Dataset' })).toHaveValue(
      second.dataset_id,
    );
    await user.click(screen.getByRole('button', { name: 'New experiment' }));
    const form = screen.getByRole('combobox', { name: 'Dataset' });
    expect(form).toHaveValue(second.dataset_id);
    await user.click(form);
    await user.click(
      screen.getByRole('option', { name: new RegExp(dataset.dataset_id) }),
    );
    expect(header).toHaveValue(dataset.dataset_id);
    await user.click(
      screen.getByRole('combobox', { name: 'Select processing operation' }),
    );
    expect(screen.getAllByRole('option')).toHaveLength(1);
    expect(
      screen.getByRole('option', { name: 'Resize + Gray' }),
    ).toHaveAttribute('aria-selected', 'true');
    await user.keyboard('{Escape}');
  });

  it('locks both dataset controls to an unconfirmed recovered submission', async () => {
    sessionStorage.setItem(
      'tachyron.active.v1',
      JSON.stringify({ key: 'recover-parity', request }),
    );
    render(<App />);
    const header = await screen.findByRole('combobox', {
      name: 'Select benchmark dataset',
    });
    await waitFor(() => expect(header).toHaveValue(request.dataset_id));
    expect(header).toBeDisabled();
    expect(screen.getByRole('combobox', { name: 'Dataset' })).toBeDisabled();
    expect(
      screen.getByRole('button', { name: 'Retry same submission' }),
    ).toBeEnabled();
  });

  it('reopens compact benchmark settings after invalid hidden dimensions without submitting', async () => {
    const user = userEvent.setup();
    const submit = vi.spyOn(api, 'startJob');
    render(<App />);
    await waitFor(() =>
      expect(screen.getByRole('combobox', { name: 'Dataset' })).toHaveValue(
        dataset.dataset_id,
      ),
    );
    const disclosure = screen.getByText('Benchmark settings');
    await user.click(disclosure);
    await user.clear(screen.getByLabelText('Width (px)'));
    await user.type(screen.getByLabelText('Width (px)'), '4097');
    await user.click(disclosure);
    expect(screen.getByLabelText('Width (px)')).not.toBeVisible();
    await user.click(screen.getByRole('button', { name: /Run experiment/ }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'dimensions from 1 to 4096',
    );
    expect(screen.getByLabelText('Width (px)')).toBeVisible();
    expect(submit).not.toHaveBeenCalled();
  });

  it('shows the overview history as a selectable table and never presents failed metrics as success', async () => {
    const user = userEvent.setup();
    vi.mocked(api.getHistory).mockResolvedValue([
      { job: job('FAILED'), summaries: detail.result?.summaries ?? null },
    ]);
    vi.spyOn(api, 'getDetail').mockResolvedValue({
      ...detail,
      job: job('FAILED'),
    });
    render(<App />);
    const table = await screen.findByRole('table');
    expect(
      within(table)
        .getAllByRole('columnheader')
        .map((el) => el.textContent),
    ).toEqual(['Run', 'Operation', 'Workers', 'Time', 'Speedup', 'Status']);
    expect(within(table).getAllByText('Unavailable')).toHaveLength(2);
    expect(within(table).getByText('failed')).toBeVisible();
    await user.click(
      within(table).getByRole('button', { name: /test-images/ }),
    );
    expect(
      await screen.findByRole('heading', {
        level: 1,
        name: 'Scaling comparison',
      }),
    ).toBeVisible();
    expect(screen.queryByText('Measured results')).not.toBeInTheDocument();
  });
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
    await screen.findByText('Datasets unavailable');
    expect(screen.getByText('Datasets unavailable')).toBeVisible();
    expect(screen.getByText('History unavailable')).toBeVisible();
    expect(
      await screen.findByRole('heading', {
        name: 'API requests need attention',
      }),
    ).toBeVisible();
    expect(screen.queryAllByRole('alert')).toHaveLength(0);
    expect(
      screen.getByText(
        'An API request failed. Recovery actions are available.',
      ),
    ).toHaveAttribute('aria-live', 'polite');
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
        datasetId={dataset.dataset_id}
        selectDataset={vi.fn()}
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
        datasetId=""
        selectDataset={vi.fn()}
        disabled={false}
        pending={undefined}
        submit={vi.fn()}
      />,
    );
    expect(
      screen.getByRole('button', { name: /Run experiment/ }),
    ).toBeDisabled();
    const user = userEvent.setup();
    await user.click(screen.getByRole('combobox', { name: 'Dataset' }));
    expect(screen.getByRole('option', { name: /unavailable/ })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
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
    vi.mocked(healthApi.getHealth).mockRestore();
    const user = userEvent.setup();
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response('secret traceback', { status: 500 }))
      .mockResolvedValueOnce(Response.json({ status: 'ok' }));
    vi.stubGlobal('fetch', fetchMock);
    const { unmount } = render(<ApiStatusHarness />);
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
