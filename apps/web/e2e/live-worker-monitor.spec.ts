import {
  expect,
  test,
  type APIRequestContext,
  type Locator,
  type Page,
} from '@playwright/test';

import { isOperationTerminal, parseOperation } from '@/api/operations';

const headers = { 'X-Tachyron-Request': '1' };

async function admit(
  request: APIRequestContext,
  dataset_id: string,
  mode: 'manual' | 'adaptive',
) {
  const response = await request.post('/api/operations', {
    headers,
    data: {
      dataset_id,
      mode,
      width: 16,
      height: 12,
      ...(mode === 'manual' && { workers: 2 }),
    },
  });
  expect(response.status()).toBe(202);
  const path = response.headers().location;
  expect(path).toMatch(/^\/api\/operations\/[\da-f-]{36}$/u);
  if (!path) throw new Error('Admission did not return a run location');
  return path;
}

async function read(request: APIRequestContext, path: string) {
  const response = await request.get(path, { headers });
  expect(response.ok()).toBe(true);
  return parseOperation(
    await response.json(),
    path.slice(path.lastIndexOf('/') + 1),
    null,
  );
}

async function attach(page: Page, path: string) {
  const monitor = page.getByRole('region', { name: 'Live Worker Monitor' });
  const id = monitor.getByLabel('Operational run ID');
  if (!(await id.isVisible()))
    await monitor.getByText('Attach existing run', { exact: true }).click();
  await id.fill(path.slice(path.lastIndexOf('/') + 1));
  await id.press('Enter');
  return monitor;
}

async function expectRunning(monitor: Locator) {
  await expect(
    monitor.getByText('Live · Running', { exact: true }),
  ).toBeVisible();
  await expect(
    monitor.getByRole('list', { name: 'Confirmed worker instances' }),
  ).toBeVisible();
}

async function checkWidths(page: Page, monitor: Locator) {
  for (const width of [320, 375, 390, 768, 1280, 1440]) {
    await page.setViewportSize({ width, height: 950 });
    expect(
      await monitor.evaluate((element) => {
        const bounds = element.getBoundingClientRect();
        return (
          document.documentElement.scrollWidth <= window.innerWidth &&
          bounds.left >= 0 &&
          bounds.right <= window.innerWidth &&
          element.scrollWidth <= element.clientWidth
        );
      }),
      `monitor and page fit at ${width}px`,
    ).toBe(true);
  }
}

test('observes a manual run and reconnects after the real event window rolls over', async ({
  page,
  request,
}, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/#experiments');
  const path = await admit(request, 'monitor-manual', 'manual');
  const monitor = await attach(page, path);
  await expectRunning(monitor);
  const placement = await monitor.evaluate((element) => {
    const job = element.parentElement?.querySelector('.job-progress');
    return (
      job !== null &&
      job !== undefined &&
      element.getBoundingClientRect().top >= job.getBoundingClientRect().bottom
    );
  });
  expect(placement).toBe(true);
  await expect(
    monitor.getByText(/Last task observation/).first(),
  ).toBeVisible();
  const running = await read(request, path);
  expect(
    running.events.some(
      (event) => event.type === 'task_started' && event.origin === 'child',
    ),
  ).toBe(true);
  expect(running.pool?.observed_workers.length).toBeGreaterThan(0);
  const pulse = monitor.locator('.monitor-worker-pulse').first();
  await expect(pulse).toHaveCSS('animation-name', 'monitor-observation');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await expect(pulse).toHaveCSS('animation-name', 'none');
  await monitor.screenshot({
    path: testInfo.outputPath('manual-running-desktop.png'),
  });

  await page.route(`**${path}**`, (route) =>
    route.abort('internetdisconnected'),
  );
  try {
    await expect(
      monitor.getByText('Reconnecting · Running', { exact: true }),
    ).toBeVisible();
    await expect
      .poll(async () => (await read(request, path)).state)
      .toBe('COMPLETED');
  } finally {
    await page.unroute(`**${path}**`);
  }

  await expect(
    monitor.getByText('Terminal snapshot · Completed', { exact: true }),
  ).toBeVisible();
  await expect(monitor.getByText(/Event window refreshed/)).toBeVisible();
  await expect(
    monitor.getByText(/earlier events left the server window/),
  ).toBeVisible();
  await expect(monitor.getByRole('progressbar')).toHaveAttribute(
    'value',
    '224',
  );
  await expect(monitor.getByText(/Exit code 0/).first()).toBeVisible();
  const terminal = await read(request, path);
  expect(terminal.progress.succeeded).toBe(224);
  expect(terminal.event_window_evictions).toBeGreaterThan(0);
  expect(terminal.events).toHaveLength(256);
  await checkWidths(page, monitor);
  await page.setViewportSize({ width: 390, height: 950 });
  await monitor.screenshot({
    path: testInfo.outputPath('manual-reconnected-mobile.png'),
  });
  await testInfo.attach('manual-terminal-snapshot', {
    body: JSON.stringify(terminal),
    contentType: 'application/json',
  });
});

test('shows adaptive trial receipts separately from final processing', async ({
  page,
  request,
}, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/#experiments');
  const path = await admit(request, 'monitor-adaptive', 'adaptive');
  const monitor = await attach(page, path);
  await expect(monitor.getByText(/^[1-9]\d* trials? finished$/u)).toBeVisible({
    timeout: 20000,
  });
  await expect(monitor.getByText(/Latest trial #/)).toBeVisible();
  await expect(monitor.getByRole('progressbar')).toHaveAttribute('value', '0');
  await expect(monitor.getByRole('progressbar')).toHaveAttribute('max', '640');
  await monitor.screenshot({
    path: testInfo.outputPath('adaptive-calibration.png'),
  });
  await expect(
    monitor.getByText('Terminal snapshot · Completed', { exact: true }),
  ).toBeVisible({ timeout: 95000 });
  await expect(
    monitor.getByText(/^(Selected|Fallback) · \d+ workers?$/u),
  ).toBeVisible();
  await expect(monitor.getByRole('progressbar')).toHaveAttribute(
    'value',
    '640',
  );
  const terminal = await read(request, path);
  expect(terminal.calibration_trials_finished).toBeGreaterThan(0);
  expect(terminal.selection).not.toBeNull();
  expect(terminal.final_execution_id).toBe(terminal.pool?.execution_id);
  expect(terminal.pool?.trial).toBeNull();
  expect(terminal.progress.succeeded).toBe(640);
  await monitor.screenshot({
    path: testInfo.outputPath('adaptive-completed.png'),
  });
  await testInfo.attach('adaptive-terminal-snapshot', {
    body: JSON.stringify(terminal),
    contentType: 'application/json',
  });
});

test('shows an actual child exit, unknown outcomes and an unavailable run', async ({
  page,
  request,
}, testInfo) => {
  await page.goto('/#experiments');
  const path = await admit(request, 'monitor-worker-exit', 'manual');
  const monitor = await attach(page, path);
  await expect(
    monitor.getByText('Terminal snapshot · Failed', { exact: true }),
  ).toBeVisible();
  const terminal = await read(request, path);
  expect(terminal.progress.unknown).toBeGreaterThan(0);
  expect(terminal.error_code).toBe('worker_failure');
  await expect(monitor.getByText(/Exit code 23/).first()).toBeVisible();
  await expect(monitor.getByRole('progressbar')).toHaveAttribute('value', '0');
  await monitor.screenshot({ path: testInfo.outputPath('worker-failure.png') });
  await testInfo.attach('worker-failure-snapshot', {
    body: JSON.stringify(terminal),
    contentType: 'application/json',
  });

  await attach(page, '/api/operations/00000000-0000-0000-0000-000000000000');
  await expect(monitor.getByText(/Run unavailable/)).toBeVisible();
  await expect(monitor.getByRole('progressbar')).toHaveCount(0);
});

test('reconciles cooperative cancellation and can attach after a fast run ends', async ({
  page,
  request,
}, testInfo) => {
  await page.goto('/#experiments');
  const path = await admit(request, 'monitor-manual', 'manual');
  const monitor = await attach(page, path);
  await expectRunning(monitor);
  const cancellation = await request.post(`${path}/cancel`, { headers });
  expect(cancellation.status()).toBe(202);
  await expect(
    monitor.getByText('Terminal snapshot · Cancelled', { exact: true }),
  ).toBeVisible();
  await expect(
    monitor.getByText('Cancellation requested.', { exact: true }),
  ).toBeVisible();
  const terminal = await read(request, path);
  expect(terminal.progress.not_started).toBeGreaterThan(0);
  expect(terminal.progress.pending).toBe(0);
  expect(terminal.cancel_requested).toBe(true);
  await monitor.screenshot({
    path: testInfo.outputPath('cooperative-cancellation.png'),
  });
  await testInfo.attach('cancelled-terminal-snapshot', {
    body: JSON.stringify(terminal),
    contentType: 'application/json',
  });

  const fast = await admit(request, 'browser-test', 'manual');
  await expect
    .poll(async () => isOperationTerminal((await read(request, fast)).state))
    .toBe(true);
  await attach(page, fast);
  await expect(
    monitor.getByText('Terminal snapshot · Completed', { exact: true }),
  ).toBeVisible();
  await expect(monitor.getByRole('progressbar')).toHaveAttribute('value', '8');
});
