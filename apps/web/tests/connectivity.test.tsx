import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';

import App from '@/App';
import * as api from '@/api/benchmarks';
import * as healthApi from '@/api/health';
import type { Job } from '@/api/contracts';

import { dataset, job, request } from './fixtures';

beforeEach(() => {
  sessionStorage.clear();
  window.history.replaceState(null, '', '/#experiments');
  vi.spyOn(healthApi, 'getHealth').mockRejectedValue(
    new Error('private connection failure'),
  );
  vi.spyOn(api, 'getDatasets').mockRejectedValue(
    new api.ApiError('Dataset request unavailable'),
  );
  vi.spyOn(api, 'getHistory').mockRejectedValue(
    new api.ApiError('History request unavailable'),
  );
});

function reconnect() {
  vi.mocked(healthApi.getHealth).mockResolvedValue({ status: 'ok' });
  vi.mocked(api.getDatasets).mockResolvedValue([dataset]);
  vi.mocked(api.getHistory).mockResolvedValue([]);
}

it('can dismiss a toast during a slow retry without losing focus or clearing the failure', async () => {
  const user = userEvent.setup();
  render(<App />);
  await screen.findByText('API offline', { exact: true });
  let fail: ((reason: Error) => void) | undefined;
  vi.mocked(healthApi.getHealth).mockImplementationOnce(
    () =>
      new Promise((_resolve, reject) => {
        fail = reject;
      }),
  );
  const checks = vi.mocked(healthApi.getHealth).mock.calls.length;
  await user.click(screen.getByRole('button', { name: 'Retry' }));
  expect(screen.getByRole('button', { name: 'Checking…' })).toHaveAttribute(
    'aria-disabled',
    'true',
  );
  await user.click(screen.getByRole('button', { name: 'Dismiss' }));
  const status = screen.getByRole('button', { name: 'Recheck API connection' });
  expect(status).toHaveFocus();
  expect(status).toHaveAttribute('aria-busy', 'true');
  await user.keyboard('{Enter}');
  expect(vi.mocked(healthApi.getHealth)).toHaveBeenCalledTimes(checks + 1);
  await act(async () => {
    fail?.(new Error('still offline'));
  });
  expect(
    screen.queryByRole('heading', { name: 'Unable to reach the local API' }),
  ).not.toBeInTheDocument();
  expect(screen.getByText('API offline', { exact: true })).toBeVisible();
  expect(screen.getByText('Dataset request unavailable')).toBeVisible();
});

it('consolidates offline startup, preserves independent error details and stays dismissed across retries', async () => {
  const user = userEvent.setup();
  render(<App />);
  const sidebar = screen.getByRole('complementary');
  expect(await within(sidebar).findByText('API offline')).toBeVisible();
  expect(screen.getByText('No active experiment')).toBeVisible();
  expect(
    within(sidebar).queryByText(/recovering|unknown|liveness|registered/i),
  ).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Active experiment')).not.toBeInTheDocument();
  expect(
    await screen.findByRole('heading', {
      name: 'Unable to reach the local API',
    }),
  ).toBeVisible();
  expect(screen.getAllByRole('button', { name: 'Dismiss' })).toHaveLength(1);
  expect(screen.queryAllByRole('alert')).toHaveLength(0);
  await user.click(screen.getByRole('button', { name: 'Dismiss' }));
  await user.click(screen.getByRole('link', { name: 'Overview' }));
  expect(screen.getByText('Dataset request unavailable')).toBeVisible();
  expect(screen.getByText('History request unavailable')).toBeVisible();
  await user.click(
    screen.getByRole('button', { name: 'Recheck API connection' }),
  );
  await within(sidebar).findByText('API offline');
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 350));
  });
  expect(
    screen.queryByRole('heading', { name: 'Unable to reach the local API' }),
  ).not.toBeInTheDocument();
  expect(screen.getByText('Dataset request unavailable')).toBeVisible();
  expect(sessionStorage.getItem('tachyron.active.v1')).toBeNull();
});

it('keeps a retained accepted job unverified through reconnection until that job responds', async () => {
  const user = userEvent.setup();
  sessionStorage.setItem(
    'tachyron.active.v1',
    JSON.stringify({ id: job().id }),
  );
  const getJob = vi
    .spyOn(api, 'getJob')
    .mockRejectedValue(new api.ApiError('Job status unavailable'));
  const startJob = vi.spyOn(api, 'startJob');
  render(<App />);
  const sidebar = screen.getByRole('complementary');
  await within(sidebar).findByText('API offline');
  expect(
    screen.getByText('Saved experiment status is unverified'),
  ).toBeVisible();
  expect(screen.queryByLabelText('Active experiment')).not.toBeInTheDocument();
  await screen.findByRole('button', { name: 'Retry' });
  reconnect();
  let confirm: ((value: Job) => void) | undefined;
  getJob.mockImplementation(
    () =>
      new Promise((resolve) => {
        confirm = resolve;
      }),
  );
  await user.click(screen.getByRole('button', { name: 'Retry' }));
  await within(sidebar).findByText('API online');
  expect(
    await screen.findByRole('heading', { name: 'Connection restored' }),
  ).toBeVisible();
  expect(screen.getByRole('button', { name: 'Dismiss' })).toHaveFocus();
  expect(
    screen.getByText('Saved experiment status is unverified'),
  ).toBeVisible();
  expect(screen.queryByLabelText('Active experiment')).not.toBeInTheDocument();
  await act(async () => {
    confirm?.(job('RUNNING'));
  });
  expect(await screen.findByText('RUNNING', { exact: true })).toBeVisible();
  expect(screen.getByLabelText('Active experiment')).toBeVisible();
  vi.mocked(healthApi.getHealth).mockRejectedValue(new Error('offline again'));
  await user.click(
    screen.getByRole('button', { name: 'Recheck API connection' }),
  );
  await within(sidebar).findByText('API offline');
  expect(
    screen.getByText(/Status unverified · last confirmed RUNNING/),
  ).toBeVisible();
  expect(screen.getByText('UNVERIFIED', { exact: true })).toBeVisible();
  expect(screen.queryByLabelText('Active experiment')).not.toBeInTheDocument();
  expect(
    JSON.parse(sessionStorage.getItem('tachyron.active.v1') ?? '{}'),
  ).toEqual({ id: job().id });
  expect(startJob).not.toHaveBeenCalled();
});

it('preserves an unconfirmed submission key when a toast is dismissed and submission is retried', async () => {
  const user = userEvent.setup();
  sessionStorage.setItem(
    'tachyron.active.v1',
    JSON.stringify({ key: 'retained-key', request }),
  );
  const submit = vi
    .spyOn(api, 'startJob')
    .mockRejectedValueOnce(new api.ApiError('Acceptance is unconfirmed'))
    .mockResolvedValue(job('COMPLETED'));
  render(<App />);
  await screen.findByRole('button', { name: 'Dismiss' });
  expect(
    screen.getByText(
      /Submission acceptance is unconfirmed\. Retry the same submission from the form/,
    ),
  ).toBeVisible();
  expect(screen.queryByLabelText('Active experiment')).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Dismiss' }));
  reconnect();
  await user.click(
    screen.getByRole('button', { name: 'Recheck API connection' }),
  );
  await screen.findByText('API online', { exact: true });
  await user.click(
    within(
      screen.getByRole('region', { name: 'Experiment Configuration' }),
    ).getByRole('button', { name: 'Retry same submission' }),
  );
  await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
  expect(
    JSON.parse(sessionStorage.getItem('tachyron.active.v1') ?? '{}'),
  ).toEqual({ key: 'retained-key', request });
  await user.click(
    within(
      screen.getByRole('region', { name: 'Experiment Configuration' }),
    ).getByRole('button', { name: 'Retry same submission' }),
  );
  expect(await screen.findByText('COMPLETED', { exact: true })).toBeVisible();
  expect(submit.mock.calls.map((call) => call[1])).toEqual([
    'retained-key',
    'retained-key',
  ]);
  expect(screen.queryByLabelText('Active experiment')).not.toBeInTheDocument();
});

it.each(['COMPLETED', 'FAILED', 'CANCELLED'] as const)(
  'keeps the activity dot off for confirmed %s work',
  async (state) => {
    reconnect();
    sessionStorage.setItem(
      'tachyron.active.v1',
      JSON.stringify({ id: job().id }),
    );
    vi.spyOn(api, 'getJob').mockResolvedValue(job(state));
    render(<App />);
    expect(await screen.findByText(state, { exact: true })).toBeVisible();
    expect(
      screen.queryByLabelText('Active experiment'),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText('UNVERIFIED', { exact: true }),
    ).not.toBeInTheDocument();
  },
);

it('keeps the footer minimal through offline and online transitions without stale secondary information', async () => {
  const user = userEvent.setup();
  reconnect();
  render(<App />);
  const sidebar = screen.getByRole('complementary');
  await within(sidebar).findByText('API online');
  expect(screen.getByRole('combobox', { name: 'Dataset' })).toHaveValue(
    dataset.dataset_id,
  );
  expect(
    within(sidebar).queryByText(/registered dataset/),
  ).not.toBeInTheDocument();
  vi.mocked(healthApi.getHealth).mockRejectedValueOnce(new Error('offline'));
  await user.click(
    screen.getByRole('button', { name: 'Recheck API connection' }),
  );
  await within(sidebar).findByText('API offline');
  expect(
    within(sidebar).queryByText(/registered dataset/),
  ).not.toBeInTheDocument();
  await user.click(
    screen.getByRole('button', { name: 'Recheck API connection' }),
  );
  await within(sidebar).findByText('API online');
  expect(
    within(sidebar).queryByText(/registered dataset/),
  ).not.toBeInTheDocument();
});

it('opens troubleshooting over the current view, retries with the shared health owner and restores focus', async () => {
  const user = userEvent.setup();
  render(<App />);
  const troubleshoot = await screen.findByRole('button', {
    name: 'Troubleshoot',
  });
  troubleshoot.focus();
  await user.keyboard('{Enter}');
  const dialog = await screen.findByRole('dialog', {
    name: 'Connection troubleshooting',
  });
  expect(dialog).toHaveAttribute('aria-modal', 'true');
  expect(
    within(dialog).getByRole('button', { name: 'Close troubleshooting' }),
  ).toHaveFocus();
  await user.keyboard('{Shift>}{Tab}{/Shift}');
  expect(
    within(dialog).getByRole('button', { name: 'Retry connection' }),
  ).toHaveFocus();
  await user.keyboard('{Tab}');
  expect(
    within(dialog).getByRole('button', { name: 'Close troubleshooting' }),
  ).toHaveFocus();
  fireEvent(dialog, new Event('close'));
  expect(dialog).toBeVisible();
  expect(window.location.hash).toBe('#experiments');
  expect(screen.getByText(/normally runs on port 5173/)).toHaveTextContent(
    '8000',
  );
  expect(screen.getByText('TACHYRON_API_URL')).toBeVisible();
  reconnect();
  await user.click(
    within(dialog).getByRole('button', { name: 'Retry connection' }),
  );
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: 'Recheck API connection' }),
    ).toHaveTextContent('API online'),
  );
  expect(within(dialog).getByText('API online')).toBeVisible();
  await user.click(within(dialog).getByRole('button', { name: 'Close' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Troubleshoot' }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: 'Recheck API connection' }),
  ).toHaveFocus();
  expect(window.location.hash).toBe('#experiments');
});

it('copies the repository-root command and exposes clipboard failures without losing the command', async () => {
  const user = userEvent.setup();
  const copy = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue();
  render(<App />);
  await user.click(await screen.findByRole('button', { name: 'Troubleshoot' }));
  const dialog = screen.getByRole('dialog');
  const command =
    'uv run --locked --project apps/server uvicorn tachyron.api:app --host 127.0.0.1 --port 8000';
  expect(within(dialog).getByText(command)).toBeVisible();
  await user.click(within(dialog).getByRole('button', { name: 'Copy' }));
  expect(copy).toHaveBeenCalledWith(command);
  expect(within(dialog).getByText('Command copied.')).toBeVisible();
  copy.mockRejectedValueOnce(new Error('private clipboard detail'));
  await user.click(within(dialog).getByRole('button', { name: 'Copy' }));
  expect(
    within(dialog).getByText(
      'Could not copy. Select the command and copy it manually.',
    ),
  ).toBeVisible();
  expect(within(dialog).getByText(command)).toBeVisible();
  expect(
    screen.queryByText('private clipboard detail'),
  ).not.toBeInTheDocument();
});

it('keeps failed and in-flight modal retries owned when closing, with no automatic reopening', async () => {
  const user = userEvent.setup();
  render(<App />);
  const trigger = await screen.findByRole('button', { name: 'Troubleshoot' });
  await user.click(trigger);
  let dialog = screen.getByRole('dialog');
  const checks = vi.mocked(healthApi.getHealth).mock.calls.length;
  let fail: ((reason: Error) => void) | undefined;
  vi.mocked(healthApi.getHealth).mockImplementationOnce(
    () =>
      new Promise((_resolve, reject) => {
        fail = reject;
      }),
  );
  await user.click(
    within(dialog).getByRole('button', { name: 'Retry connection' }),
  );
  const checking = within(dialog).getByRole('button', { name: 'Checking…' });
  expect(checking).toHaveAttribute('aria-disabled', 'true');
  await user.click(checking);
  expect(vi.mocked(healthApi.getHealth)).toHaveBeenCalledTimes(checks + 1);
  await act(async () => {
    fail?.(new Error('unavailable'));
  });
  expect(within(dialog).getByText('API offline')).toBeVisible();
  fireEvent(dialog, new Event('cancel', { cancelable: true }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
  await user.click(trigger);
  dialog = screen.getByRole('dialog');
  let finish: ((value: { status: 'ok' }) => void) | undefined;
  vi.mocked(healthApi.getHealth).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  await user.click(
    within(dialog).getByRole('button', { name: 'Retry connection' }),
  );
  await user.click(
    within(dialog).getByRole('button', { name: 'Close troubleshooting' }),
  );
  expect(
    screen.getByRole('button', { name: 'Recheck API connection' }),
  ).toHaveFocus();
  expect(document.documentElement.style.overflow).toBe('');
  await act(async () => {
    finish?.({ status: 'ok' });
  });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByText('API online', { exact: true })).toBeVisible();
  expect(sessionStorage.getItem('tachyron.active.v1')).toBeNull();
});

it('omits toast Retry for a rejected configuration that requires user correction', async () => {
  const user = userEvent.setup();
  reconnect();
  vi.spyOn(api, 'startJob').mockRejectedValue(
    new api.ApiError('Configuration rejected', 422),
  );
  render(<App />);
  await waitFor(() =>
    expect(screen.getByRole('combobox', { name: 'Dataset' })).toHaveValue(
      dataset.dataset_id,
    ),
  );
  await user.click(screen.getByRole('button', { name: /Run experiment/ }));
  const toast = await screen.findByRole('region', { name: 'API notification' });
  expect(within(toast).getByRole('button', { name: 'Dismiss' })).toBeVisible();
  expect(
    within(toast).queryByRole('button', { name: 'Retry' }),
  ).not.toBeInTheDocument();
  expect(screen.getAllByText('Configuration rejected').length).toBeGreaterThan(
    0,
  );
});
