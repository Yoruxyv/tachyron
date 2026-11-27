import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from '@playwright/test';

import { isOperationTerminal, parseOperation } from '@/api/operations';

const headers = { 'X-Tachyron-Request': '1' };
const dataset = 'monitor-manual';
const manualBody = {
  dataset_id: dataset,
  mode: 'manual',
  workers: 2,
  width: 16,
  height: 12,
};

function monitor(page: Page) {
  return page.getByRole('region', { name: 'Live Worker Monitor' });
}

async function choose(page: Page, name: string, option: RegExp) {
  await page.getByRole('combobox', { name, exact: true }).click();
  await page.getByRole('option', { name: option }).click();
}

async function configure(
  page: Page,
  mode: 'manual' | 'adaptive',
  name: string,
) {
  await page.goto('/#experiments');
  await choose(
    page,
    'Execution mode',
    mode === 'manual' ? /^Manual/u : /^Adaptive/u,
  );
  await choose(page, 'Dataset', new RegExp(`^${name} ·`, 'u'));
  await page.getByLabel('Width (px)').fill('16');
  await page.getByLabel('Height (px)').fill('12');
  if (mode === 'manual') await choose(page, 'Worker count', /^2 workers$/u);
}

async function status(request: APIRequestContext, runId: string) {
  const response = await request.get(`/api/operations/${runId}`, { headers });
  expect(response.status()).toBe(200);
  return parseOperation(await response.json(), runId, null);
}

async function accepted(page: Page, action: () => Promise<void>) {
  const response = page.waitForResponse(
    (result) =>
      result.url().endsWith('/api/operations') &&
      result.request().method() === 'POST',
  );
  await action();
  const result = await response;
  expect(result.status()).toBe(202);
  const body: unknown = await result.json();
  if (
    typeof body !== 'object' ||
    body === null ||
    !('run_id' in body) ||
    typeof body.run_id !== 'string'
  ) {
    throw new Error('The operation did not return its identity.');
  }
  await expect(
    monitor(page).getByText(body.run_id, { exact: true }),
  ).toBeVisible();
  expect(result.request().headers()['x-tachyron-request']).toBe('1');
  return { id: body.run_id, request: result.request() };
}

test('starts and automatically attaches a fixed run, recovers on navigation and reload, then cancels', async ({
  page,
  request,
}, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await configure(page, 'manual', dataset);
  const start = page.getByRole('button', { name: 'Start manual run' });
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
  const { id, request: launch } = await accepted(page, () =>
    start.press('Enter'),
  );
  expect(launch.postDataJSON()).toEqual(manualBody);
  await monitor(page)
    .getByRole('button', { name: 'Copy run ID' })
    .press('Enter');
  await expect(monitor(page).getByText('Run ID copied.')).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(id);
  await expect(start).toBeDisabled();
  await expect(page.getByText('Last verified · Running')).toBeVisible();
  await expect(
    monitor(page).getByText('Live · Running', { exact: true }),
  ).toBeVisible();
  await expect(
    monitor(page).getByRole('list', { name: 'Confirmed worker instances' }),
  ).toBeVisible();
  await page.screenshot({
    fullPage: true,
    path: testInfo.outputPath('manual-dashboard.png'),
  });

  await page.getByRole('link', { name: 'Overview', exact: true }).click();
  await page.getByRole('link', { name: 'Experiments', exact: true }).click();
  await expect(monitor(page).getByText(id, { exact: true })).toBeVisible();
  await page.reload();
  await expect(monitor(page).getByText(id, { exact: true })).toBeVisible();
  await page
    .getByRole('link', { name: /View run in Live Worker Monitor/u })
    .press('Enter');
  await expect(monitor(page)).toBeFocused();
  const stop = monitor(page).getByRole('button', { name: 'Cancel operation' });
  await stop.click();
  await expect(
    monitor(page).getByText('Terminal snapshot · Cancelled', { exact: true }),
  ).toBeVisible();
  const terminal = await status(request, id);
  expect(terminal.cancel_requested).toBe(true);
  await expect(page.getByText('Last verified · Cancelled')).toBeVisible();
  expect(terminal.progress.not_started).toBeGreaterThan(0);
  expect(terminal.progress.pending).toBe(0);
  await expect(start).toBeEnabled();
  for (const width of [320, 375, 390, 768, 1280, 1440]) {
    await page.setViewportSize({ width, height: 950 });
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBe(width);
  }
  await page.setViewportSize({ width: 390, height: 950 });
  await expect(
    page.getByRole('combobox', { name: 'Execution mode' }),
  ).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(
    390,
  );
  await monitor(page).screenshot({
    path: testInfo.outputPath('manual-cancelled-mobile.png'),
  });
});

test('starts an experimental adaptive run and displays fallback separately from output', async ({
  page,
  request,
}, testInfo) => {
  await configure(page, 'adaptive', 'browser-test');
  await expect(page.getByText(/spends time calibrating/u)).toBeVisible();
  await expect(
    page.getByRole('combobox', { name: 'Worker count' }),
  ).toHaveCount(0);
  await expect(page.getByLabel('Measured repetitions per worker')).toHaveCount(
    0,
  );
  const { id, request: launch } = await accepted(page, () =>
    page.getByRole('button', { name: 'Start adaptive run' }).click(),
  );
  expect(launch.postDataJSON()).toEqual({
    dataset_id: 'browser-test',
    mode: 'adaptive',
    width: 16,
    height: 12,
  });
  await expect(
    monitor(page).getByText('Terminal snapshot · Completed', { exact: true }),
  ).toBeVisible();
  await expect(monitor(page).getByText('Fallback · 1 worker')).toBeVisible();
  await expect(monitor(page).getByRole('progressbar')).toHaveAttribute(
    'value',
    '8',
  );
  const terminal = await status(request, id);
  expect(terminal.selection?.fallback).toBe(true);
  expect(terminal.progress.succeeded).toBe(8);
  await page.screenshot({
    fullPage: true,
    path: testInfo.outputPath('adaptive-fallback-dashboard.png'),
  });
});

test('keeps real adaptive trials separate from final work launched in the dashboard', async ({
  page,
  request,
}, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await configure(page, 'adaptive', 'monitor-adaptive');
  const { id } = await accepted(page, () =>
    page.getByRole('button', { name: 'Start adaptive run' }).click(),
  );
  await expect(
    monitor(page).getByText(/^[1-9]\d* trials? finished$/u),
  ).toBeVisible({ timeout: 20000 });
  await expect(monitor(page).getByRole('progressbar')).toHaveAttribute(
    'value',
    '0',
  );
  await page.screenshot({
    fullPage: true,
    path: testInfo.outputPath('adaptive-calibrating-dashboard.png'),
  });
  await expect(
    monitor(page).getByText('Terminal snapshot · Completed', { exact: true }),
  ).toBeVisible({ timeout: 95000 });
  await expect(
    monitor(page).getByText(/^(Selected|Fallback) · \d+ workers?$/u),
  ).toBeVisible();
  const terminal = await status(request, id);
  expect(terminal.calibration_trials_finished).toBeGreaterThan(0);
  expect(terminal.progress.succeeded).toBe(640);
  await testInfo.attach('adaptive-dashboard-terminal', {
    body: JSON.stringify(terminal),
    contentType: 'application/json',
  });
});

test('displays a real admission conflict and can start after it clears', async ({
  page,
  request,
}) => {
  await configure(page, 'manual', dataset);
  const start = page.getByRole('button', { name: 'Start manual run' });
  await page.getByLabel('Width (px)').fill('0');
  await start.click();
  await expect(page.getByText(/dimensions from 1 to 4096/u)).toBeVisible();
  await page.getByLabel('Width (px)').fill('16');
  const create = await request.post('/api/operations', {
    headers,
    data: manualBody,
  });
  expect(create.status()).toBe(202);
  const path = create.headers().location;
  if (!path) throw new Error('Expected an operation location.');
  const id = path.slice('/api/operations/'.length);
  await start.click();
  await expect(
    page.getByRole('alert').filter({ hasText: /another workload is active/u }),
  ).toBeVisible();
  await expect(start).toBeEnabled();
  await request.post(`${path}/cancel`, { headers });
  await expect
    .poll(async () => isOperationTerminal((await status(request, id)).state))
    .toBe(true);

  await choose(page, 'Dataset', /^browser-test ·/u);
  await accepted(page, () => start.click());
  await expect(
    monitor(page).getByText('Terminal snapshot · Completed', { exact: true }),
  ).toBeVisible();
});

test('preserves uncertain admission after a lost acceptance response and reload', async ({
  page,
  request,
}) => {
  await configure(page, 'manual', 'browser-test');
  let submissions = 0;
  let admittedId: string | undefined;
  await page.route('**/api/operations', async (route) => {
    submissions += 1;
    const result = await route.fetch();
    expect(result.status()).toBe(202);
    admittedId = result.headers().location?.slice('/api/operations/'.length);
    await route.abort('connectionreset');
  });
  const start = page.getByRole('button', { name: 'Start manual run' });
  await start.click();
  const uncertainty = page
    .getByRole('alert')
    .filter({ hasText: /may have accepted/u });
  await expect(uncertainty).toBeVisible();
  expect(admittedId).toBeDefined();
  await page.reload();
  await expect(uncertainty).toBeVisible();
  await expect(start).toBeDisabled();
  await choose(page, 'Execution mode', /^Benchmark$/u);
  await expect(
    page.getByRole('heading', { name: 'Job execution' }),
  ).toBeVisible();
  await choose(page, 'Execution mode', /^Manual$/u);
  await expect(uncertainty).toBeVisible();
  if (!admittedId) throw new Error('The real server did not return a run ID.');
  const id = monitor(page).getByLabel('Operational run ID');
  await expect(id).toBeHidden();
  await monitor(page)
    .getByText('Attach existing run', { exact: true })
    .press('Enter');
  await id.fill(admittedId);
  await id.press('Enter');
  await expect(
    monitor(page).getByText('Terminal snapshot · Completed', { exact: true }),
  ).toBeVisible();
  expect(submissions).toBe(1);
  expect((await status(request, admittedId)).progress.succeeded).toBe(8);
  await page.getByRole('button', { name: 'Allow a new submission' }).click();
  await expect(uncertainty).toHaveCount(0);
  await expect(start).toBeEnabled();
});
