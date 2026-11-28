import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import * as api from '@/api/operations';
import LiveWorkerMonitor from '@/features/operations/LiveWorkerMonitor';

import {
  instanceId,
  observation,
  operation,
  otherRunId,
  poolId,
  runId,
} from './operation-fixtures';

describe('live worker monitor presentation', () => {
  it('copies the monitored identity and keeps it selectable if clipboard access fails', async () => {
    const user = userEvent.setup();
    const copy = vi
      .spyOn(navigator.clipboard, 'writeText')
      .mockResolvedValueOnce()
      .mockRejectedValueOnce(new Error('Private browser detail'));
    vi.spyOn(api, 'getOperation').mockResolvedValue(
      api.parseOperation(operation(), runId, null),
    );
    render(<LiveWorkerMonitor acceptedRunId={runId} />);
    expect(screen.getByLabelText('Operational run ID')).not.toBeVisible();
    const button = screen.getByRole('button', { name: 'Copy run ID' });
    await user.click(button);
    expect(copy).toHaveBeenCalledWith(runId);
    expect(screen.getByText('Run ID copied.')).toBeVisible();
    await user.click(button);
    expect(screen.getByText(/Clipboard unavailable/)).toBeVisible();
    expect(
      screen.queryByText('Private browser detail'),
    ).not.toBeInTheDocument();
    expect(screen.getByText(runId)).toBeVisible();
  });

  it('renders observed trial activity independently of final progress and uses honest readiness language', async () => {
    const snapshot = api.parseOperation(
      operation({
        mode: 'adaptive',
        requested_workers: null,
        phase: 'calibration',
        pool: {
          execution_id: poolId,
          requested_workers: 2,
          state: 'RUNNING',
          trial: { index: 0, repetition: 1 },
          progress: {
            total: 16,
            pending: 12,
            succeeded: 4,
            failed: 0,
            unknown: 0,
            not_started: 0,
          },
          observed_workers: [
            { instance_id: instanceId, pid: 12, state: 'READY' },
          ],
        },
        snapshot_sequence: 3,
        events: [
          observation(1),
          observation(2, {
            type: 'task_submitted',
            phase: 'calibration',
            execution_id: poolId,
            index: 1,
            trial_index: 0,
          }),
          observation(3, {
            type: 'worker_ready',
            origin: 'child',
            phase: 'calibration',
            execution_id: poolId,
            instance_id: instanceId,
            pid: 12,
            trial_index: 0,
          }),
        ],
      }),
      runId,
      null,
    );
    const get = vi.spyOn(api, 'getOperation').mockResolvedValue(snapshot);
    const user = userEvent.setup();
    render(<LiveWorkerMonitor />);
    expect(get).not.toHaveBeenCalled();
    await user.click(screen.getByText('Attach existing run', { exact: true }));
    const input = screen.getByLabelText('Operational run ID');
    await user.type(input, 'invalid{Enter}');
    expect(screen.getByRole('alert')).toHaveTextContent('UUID');
    expect(get).not.toHaveBeenCalled();
    await user.clear(input);
    await user.type(input, `${runId.toUpperCase()}{Enter}`);
    const pool = await screen.findByRole('region', { name: 'Worker pool' });
    expect(pool).toHaveTextContent('1 confirmed · 2 requested');
    expect(pool).toHaveTextContent('PID 12 Ready');
    const lifecycle = within(pool).getByRole('list', {
      name: 'Retained lifecycle observations',
    });
    expect(lifecycle).toHaveTextContent('Startup event unavailable');
    expect(lifecycle).toHaveTextContent('Readiness observed');
    expect(lifecycle).toHaveTextContent('Exit event unavailable');
    expect(pool).not.toHaveTextContent('Last task observation');
    expect(pool).toHaveTextContent('Current assignment may be unknown');
    expect(
      screen.getByText(/4 \/ 16 trial outcomes acknowledged/),
    ).toBeVisible();
    expect(screen.getByRole('progressbar')).toHaveAttribute('value', '0');
    expect(screen.getByRole('progressbar')).toHaveAttribute('max', '8');
    const events = screen.getByRole('list', {
      name: 'Recent execution events',
    });
    expect(within(events).getByText('Task submitted')).toBeVisible();
    await user.click(screen.getByRole('combobox', { name: 'Show' }));
    await user.click(screen.getByRole('option', { name: 'Worker lifecycle' }));
    expect(within(events).getByText('Worker readiness observed')).toBeVisible();
    expect(
      within(events).queryByText('Task submitted'),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole('combobox', { name: 'Show' }));
    await user.click(screen.getByRole('option', { name: 'Task observations' }));
    expect(within(events).getByText('Task submitted')).toBeVisible();
    expect(
      within(events).queryByText('Worker readiness observed'),
    ).not.toBeInTheDocument();
  });

  it.each([
    {
      status: 'INCONCLUSIVE',
      fallback: true,
      workers: 1,
      reason: 'budget',
      label: 'Fallback · 1 worker',
    },
    {
      status: 'SELECTED',
      fallback: false,
      workers: 2,
      reason: 'measured',
      label: 'Selected · 2 workers',
    },
  ])(
    'replaces the previous run and shows $label with terminal unknowns',
    async ({ label, ...selection }) => {
      const progress = {
        total: 8,
        pending: 0,
        succeeded: 2,
        failed: 0,
        unknown: 3,
        not_started: 3,
      };
      const terminal = api.parseOperation(
        operation({
          run_id: otherRunId,
          dataset_id: 'second-dataset',
          state: 'UNCONFIRMED',
          mode: 'adaptive',
          phase: 'execution',
          requested_workers: null,
          final_execution_id: poolId,
          cancel_requested: true,
          selection: { ...selection, profiled_inputs: 8 },
          progress,
          pool: {
            execution_id: poolId,
            requested_workers: selection.workers,
            trial: null,
            state: 'UNCONFIRMED',
            progress,
            observed_workers: [],
          },
          coverage: 'best_effort_with_gaps',
          transport_loss_count_unquantified: true,
        }),
        otherRunId,
        null,
      );
      const get = vi
        .spyOn(api, 'getOperation')
        .mockImplementation((id, after) => {
          if (id === otherRunId) return Promise.resolve(terminal);
          const body = after === null ? operation() : operation({ events: [] });
          return Promise.resolve(api.parseOperation(body, runId, after));
        });
      const user = userEvent.setup();
      render(<LiveWorkerMonitor />);
      await user.click(
        screen.getByText('Attach existing run', { exact: true }),
      );
      const input = screen.getByLabelText('Operational run ID');
      await user.type(input, `${runId}{Enter}`);
      await screen.findByText(/browser-test/);
      const oldSignal = get.mock.calls[0]?.[2];
      await user.clear(input);
      await user.type(input, `${otherRunId}{Enter}`);
      expect(await screen.findByText(/outcome unconfirmed/i)).toBeVisible();
      expect(oldSignal?.aborted).toBe(true);
      expect(get).toHaveBeenLastCalledWith(
        otherRunId,
        null,
        expect.any(AbortSignal),
      );
      expect(screen.queryByText(/browser-test/)).not.toBeInTheDocument();
      expect(screen.getByText(label)).toBeVisible();
      expect(
        screen.getByText('Cancellation requested.', { exact: true }),
      ).toBeVisible();
      expect(
        screen.getByText('Delivery loss could not be fully counted.'),
      ).toBeVisible();
      expect(screen.getByRole('progressbar')).toHaveAttribute('value', '2');
      await user.click(screen.getByRole('button', { name: 'Detach' }));
      expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
      expect(input).toHaveFocus();
    },
  );
});
