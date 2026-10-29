import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ComponentProps } from 'react';
import { expect, it, vi } from 'vitest';

import ApiNotifications from '@/features/diagnostics/ApiNotifications';

import { job, request } from './fixtures';

type Props = ComponentProps<typeof ApiNotifications>;

function sources(): Props {
  return {
    health: {
      status: 'API online',
      checking: false,
      failedAt: 10,
      check: vi.fn().mockResolvedValue(true),
    },
    catalog: {
      datasets: [],
      acceptImported: vi.fn(),
      error: '',
      loading: false,
      verifiedAt: 20,
      reload: vi.fn(),
    },
    history: {
      items: [],
      offset: 0,
      setOffset: vi.fn(),
      selectedId: '',
      select: vi.fn(),
      detail: null,
      error: '',
      detailError: '',
      loading: false,
      initialLoading: false,
      detailLoading: false,
      verifiedAt: 20,
      reload: vi.fn(),
    },
    execution: {
      job: null,
      pending: null,
      busy: false,
      error: '',
      verifiedAt: 0,
      storageWarning: '',
      polling: false,
      submit: vi.fn().mockResolvedValue(undefined),
      cancel: vi.fn().mockResolvedValue(undefined),
      resume: vi.fn(),
      recovering: false,
      coolingDown: false,
    },
  };
}

it('announces connection recovery once while independent requests remain failed', async () => {
  const user = userEvent.setup();
  let input = sources();
  input = {
    ...input,
    health: { ...input.health, status: 'API unavailable' },
    catalog: { ...input.catalog, error: 'Datasets failed' },
    history: { ...input.history, error: 'History failed' },
  };
  const { rerender } = render(<ApiNotifications {...input} />);
  expect(screen.getByRole('heading')).toHaveTextContent(
    'Unable to reach the local API',
  );
  await user.click(screen.getByRole('button', { name: 'Dismiss' }));
  rerender(<ApiNotifications {...input} />);
  expect(screen.queryByRole('heading')).not.toBeInTheDocument();
  input = { ...input, health: { ...input.health, status: 'API online' } };
  rerender(<ApiNotifications {...input} />);
  expect(screen.getByRole('heading')).toHaveTextContent('Connection restored');
  await user.click(screen.getByRole('button', { name: 'Dismiss' }));
  input = {
    ...input,
    catalog: { ...input.catalog, error: '' },
    history: { ...input.history, error: '' },
  };
  rerender(<ApiNotifications {...input} />);
  expect(screen.queryByRole('heading')).not.toBeInTheDocument();
  input = {
    ...input,
    history: { ...input.history, error: 'New independent history failure' },
  };
  rerender(<ApiNotifications {...input} />);
  expect(screen.getByRole('heading')).toHaveTextContent(
    'API requests need attention',
  );
});

it('waits for actual job verification after clearing a retained job error', () => {
  let input = sources();
  input = {
    ...input,
    execution: {
      ...input.execution,
      job: job('RUNNING'),
      error: 'Status request failed',
    },
  };
  const { rerender } = render(<ApiNotifications {...input} />);
  expect(screen.getByText('Status request failed')).toBeVisible();
  input = {
    ...input,
    execution: { ...input.execution, error: '', polling: true },
  };
  rerender(<ApiNotifications {...input} />);
  expect(
    screen.queryByRole('heading', { name: 'Requests available' }),
  ).not.toBeInTheDocument();
  input = { ...input, execution: { ...input.execution, verifiedAt: 30 } };
  rerender(<ApiNotifications {...input} />);
  expect(screen.getByRole('heading')).toHaveTextContent('Requests available');
});

it('refreshes only failed or stale resources after a successful retry', async () => {
  const user = userEvent.setup();
  const input = sources();
  input.history.error = 'Selected evidence failed';
  render(<ApiNotifications {...input} />);
  // The episode checks liveness once before the explicit retry.
  expect(input.health.check).toHaveBeenCalledTimes(1);
  await user.click(screen.getByRole('button', { name: 'Retry' }));
  expect(input.catalog.reload).not.toHaveBeenCalled();
  expect(input.history.reload).toHaveBeenCalledTimes(1);
  expect(input.execution.resume).not.toHaveBeenCalled();
  expect(input.execution.submit).not.toHaveBeenCalled();
});

it('does not refresh resources after a failed probe or after unmounting during a probe', async () => {
  const user = userEvent.setup();
  const input = sources();
  input.health.status = 'API unavailable';
  vi.mocked(input.health.check).mockResolvedValueOnce(false);
  const { unmount } = render(<ApiNotifications {...input} />);
  await user.click(screen.getByRole('button', { name: 'Retry' }));
  expect(input.catalog.reload).not.toHaveBeenCalled();
  expect(input.history.reload).not.toHaveBeenCalled();
  let finish: ((result: boolean) => void) | undefined;
  vi.mocked(input.health.check).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  await user.click(screen.getByRole('button', { name: 'Retry' }));
  unmount();
  await act(async () => {
    finish?.(true);
  });
  expect(input.catalog.reload).not.toHaveBeenCalled();
  expect(input.history.reload).not.toHaveBeenCalled();
  expect(input.execution.resume).not.toHaveBeenCalled();
});

it('keeps a retained job retry unavailable during its server cooldown', () => {
  const input = sources();
  input.execution.job = job('RUNNING');
  input.execution.error = 'Retry later';
  input.execution.coolingDown = true;
  render(<ApiNotifications {...input} />);
  expect(screen.getByRole('button', { name: 'Retry' })).toHaveAttribute(
    'aria-disabled',
    'true',
  );
  expect(input.execution.submit).not.toHaveBeenCalled();
  expect(input.execution.resume).not.toHaveBeenCalled();
});

it('delegates an unconfirmed submission to its existing owner once during a slow retry', async () => {
  const user = userEvent.setup();
  const input = sources();
  input.health.status = 'API unavailable';
  input.execution.pending = { key: 'retained-key', request };
  input.execution.error = 'Acceptance is unconfirmed';
  let finish: (() => void) | undefined;
  vi.mocked(input.execution.submit).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  render(<ApiNotifications {...input} />);
  await user.click(screen.getByRole('button', { name: 'Retry' }));
  const checking = screen.getByRole('button', { name: 'Checking…' });
  await user.click(checking);
  expect(input.health.check).toHaveBeenCalledTimes(1);
  expect(input.execution.submit).toHaveBeenCalledExactlyOnceWith(request);
  expect(input.execution.resume).not.toHaveBeenCalled();
  expect(input.execution.pending).toEqual({ key: 'retained-key', request });
  await act(async () => {
    finish?.();
  });
  expect(screen.getByRole('button', { name: 'Retry' })).toHaveAttribute(
    'aria-disabled',
    'false',
  );
});

it('shares import feedback without repeating dismissed episodes during polling, and clears stale selection errors', async () => {
  const user = userEvent.setup();
  const input = sources();
  const feedback = {
    id: 1,
    kind: 'failure' as const,
    title: 'Unsupported files detected',
    message: 'Choose another folder.',
  };
  const { rerender } = render(
    <ApiNotifications {...input} importFeedback={feedback} />,
  );
  expect(screen.getByRole('heading')).toHaveTextContent(feedback.title);
  expect(
    screen.queryByRole('button', { name: 'Retry' }),
  ).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Dismiss' }));
  rerender(
    <ApiNotifications
      {...input}
      execution={{ ...input.execution, verifiedAt: 30 }}
      importFeedback={feedback}
    />,
  );
  expect(screen.queryByRole('heading')).not.toBeInTheDocument();
  rerender(
    <ApiNotifications {...input} importFeedback={{ ...feedback, id: 2 }} />,
  );
  expect(screen.getByRole('heading')).toHaveTextContent(feedback.title);
  rerender(<ApiNotifications {...input} importFeedback={null} />);
  expect(screen.queryByRole('heading')).not.toBeInTheDocument();
});

it('keeps an API failure visible while retaining import feedback for after dismissal', async () => {
  const user = userEvent.setup();
  const input = sources();
  input.health.status = 'API unavailable';
  render(
    <ApiNotifications
      {...input}
      importFeedback={{
        id: 1,
        kind: 'failure',
        title: 'Import outcome unconfirmed',
        message: 'Check status.',
      }}
    />,
  );
  expect(screen.getByRole('heading')).toHaveTextContent(
    'Unable to reach the local API',
  );
  await user.click(screen.getByRole('button', { name: 'Dismiss' }));
  expect(screen.getByRole('heading')).toHaveTextContent(
    'Import outcome unconfirmed',
  );
});

it('expires a registration toast once and does not recreate it on unrelated state changes', async () => {
  vi.useFakeTimers();
  const input = sources();
  const feedback = {
    id: 1,
    kind: 'recovery' as const,
    title: 'Dataset registered',
    message: 'Available for selection.',
  };
  const { rerender } = render(
    <ApiNotifications {...input} importFeedback={feedback} />,
  );
  expect(screen.getByRole('heading')).toHaveTextContent('Dataset registered');
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  rerender(<ApiNotifications {...input} importFeedback={feedback} />);
  expect(screen.queryByRole('heading')).not.toBeInTheDocument();
});

it('shows confirmation feedback without duplicating the focused field announcement', () => {
  render(
    <ApiNotifications
      {...sources()}
      importFeedback={{
        id: 1,
        kind: 'failure',
        title: 'Confirm the workload',
        message:
          'Acknowledge the included images and exclusions before importing.',
        announce: false,
      }}
    />,
  );
  expect(screen.getByRole('heading')).toHaveTextContent('Confirm the workload');
  expect(screen.getByRole('status')).toBeEmptyDOMElement();
});
