import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { parseOperation } from '@/api/operations';
import TaskObservations from '@/features/operations/TaskObservations';

import {
  observation,
  operation,
  otherRunId,
  poolId,
  runId,
} from './operation-fixtures';

function snapshot(...events: Record<string, unknown>[]) {
  const progress = {
    total: 8,
    pending: 8,
    succeeded: 0,
    failed: 0,
    unknown: 0,
    not_started: 0,
  };
  return parseOperation(
    operation({
      phase: 'execution',
      final_execution_id: poolId,
      pool: {
        execution_id: poolId,
        requested_workers: 2,
        state: 'RUNNING',
        trial: null,
        progress,
        observed_workers: [],
      },
      snapshot_sequence: events.length,
      events: events.map((event, index) =>
        observation(index + 1, {
          phase: 'execution',
          execution_id: poolId,
          ...event,
        }),
      ),
    }),
    runId,
    null,
  );
}

function task(index: number) {
  return within(
    screen.getByRole('heading', { name: `Task #${index}` }).closest('li')!,
  );
}

describe('execution flow', () => {
  it('keeps dispatch, confirmed starts, and receipt acknowledgement independent and pool-scoped', () => {
    const view = render(
      <TaskObservations
        snapshot={snapshot(
          {
            type: 'task_started',
            execution_id: otherRunId,
            trial_index: 0,
            phase: 'calibration',
            origin: 'child',
            index: 0,
            pid: 42,
          },
          { type: 'task_submitted', index: 0 },
          { type: 'task_result_received', index: 0, pid: 83 },
          { type: 'task_started', index: 1, origin: 'child', pid: 84 },
          { type: 'task_succeeded', index: 1 },
          { type: 'task_started', index: 2 },
          { type: 'task_failed', index: 2 },
        )}
        animateAfter={null}
        stale={false}
      />,
    );
    expect(task(0).getByText('Submitted')).toBeVisible();
    expect(task(0).getByText('No observation')).toBeVisible();
    expect(
      task(0).getByText(/Result received · acknowledgement unobserved/),
    ).toBeVisible();
    expect(task(1).getByText('Start observed')).toBeVisible();
    expect(task(1).getByText('PID 84')).toBeVisible();
    expect(task(1).getByText('Success acknowledged')).toBeVisible();
    expect(task(2).getAllByText('No observation')).toHaveLength(2);
    expect(task(2).getByText('Failure acknowledged')).toBeVisible();
    expect(screen.queryByText(/PID (42|83)/u)).not.toBeInTheDocument();
    expect(view.container.querySelectorAll('.monitor-task-pulse')).toHaveLength(
      0,
    );
  });

  it('bounds task rows and pulses only new observations from continuous polling', () => {
    const current = snapshot(
      { type: 'task_submitted', index: 4 },
      { type: 'task_submitted', index: 3 },
      { type: 'task_submitted', index: 2 },
      { type: 'task_result_received', index: 3 },
      { type: 'task_not_started', index: 1 },
      { type: 'submission_unknown', index: 0 },
      { type: 'task_unknown', index: 0 },
      { type: 'task_succeeded', index: 3 },
    );
    const view = render(
      <TaskObservations snapshot={current} animateAfter={6} stale={false} />,
    );
    expect(screen.getAllByRole('listitem')).toHaveLength(4);
    expect(
      screen.queryByRole('heading', { name: 'Task #4' }),
    ).not.toBeInTheDocument();
    expect(task(0).getByText('Submission unconfirmed')).toBeVisible();
    expect(task(0).getByText('Outcome unknown')).toBeVisible();
    expect(task(1).getByText('Not started')).toBeVisible();
    expect(task(3).getByText('Submitted')).toBeVisible();
    expect(task(3).getByText('Success acknowledged')).toBeVisible();
    expect(task(3).queryByText(/Result received/)).not.toBeInTheDocument();
    expect(view.container.querySelectorAll('.monitor-task-pulse')).toHaveLength(
      2,
    );

    view.rerender(
      <TaskObservations snapshot={current} animateAfter={null} stale />,
    );
    expect(screen.getByText(/Last verified task observations/)).toBeVisible();
    expect(view.container.querySelectorAll('.monitor-task-pulse')).toHaveLength(
      0,
    );

    const resynced = snapshot({ type: 'task_succeeded', index: 7 });
    view.rerender(
      <TaskObservations
        snapshot={resynced}
        animateAfter={null}
        stale={false}
      />,
    );
    expect(screen.getAllByRole('listitem')).toHaveLength(1);
    expect(task(7).getAllByText('No observation')).toHaveLength(2);
    expect(view.container.querySelectorAll('.monitor-task-pulse')).toHaveLength(
      0,
    );
  });
});
