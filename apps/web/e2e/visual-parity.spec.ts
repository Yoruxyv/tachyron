import { expect, test } from '@playwright/test';

import { dataset, detail } from '../tests/fixtures';

test('keeps dropdowns compact, anchored, accessible and above cards at every layout', async ({
  page,
}, testInfo) => {
  let populated = false;
  await page.route('**/health', (route) =>
    route.fulfill({ json: { status: 'ok' } }),
  );
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/datasets')
      await route.fulfill({
        json: populated
          ? [dataset, { ...dataset, dataset_id: 'second-registered-dataset' }]
          : [],
      });
    else if (path === '/api/benchmarks')
      await route.fulfill({
        json: populated
          ? [{ job: detail.job, summaries: detail.result?.summaries }]
          : [],
      });
    else await route.fulfill({ json: detail });
  });
  await page.goto('/#history');
  const header = page.getByRole('combobox', {
    name: 'Select benchmark dataset',
  });
  const saved = page.getByRole('combobox', { name: 'Saved comparison' });
  await expect(header).toBeDisabled();
  await expect(header).toHaveText('Choose a registered dataset');
  await expect(
    page.getByText('Choose an experiment', { exact: true }),
  ).toHaveCount(1);
  await expect(saved).toBeDisabled();
  populated = true;
  await page.reload();
  await expect(saved).toHaveJSProperty('value', detail.job.id);
  const boxes = [];
  for (const width of [1440, 1024, 768, 375, 320]) {
    await page.setViewportSize({ width, height: 900 });
    // Resize events precede animation frames and intentionally close open selects.
    await page.evaluate(
      () =>
        new Promise<void>((resolve) => requestAnimationFrame(() => resolve())),
    );
    const controls = await page.evaluate(() => {
      const pill = document.querySelector('.header-selects');
      if (!pill) throw new Error('Missing header selector group');
      const group = pill.getBoundingClientRect();
      return [...pill.querySelectorAll('button')].map((button) => {
        const rect = button.getBoundingClientRect();
        const text = button.querySelector('.select-value');
        if (!text) throw new Error('Missing selected value');
        return {
          x: rect.x,
          right: rect.right,
          y: rect.y,
          height: rect.height,
          groupLeft: group.left,
          groupRight: group.right,
          whiteSpace: getComputedStyle(text).whiteSpace,
          textHeight: text.getBoundingClientRect().height,
        };
      });
    });
    expect(controls).toHaveLength(2);
    for (const control of controls) {
      expect(control.height).toBe(34);
      expect(control.whiteSpace).toBe('nowrap');
      expect(control.textHeight).toBeLessThanOrEqual(17);
      expect(control.x).toBeGreaterThanOrEqual(control.groupLeft);
      expect(control.right).toBeLessThanOrEqual(control.groupRight);
    }
    expect(controls[0]?.right).toBeLessThan(controls[1]?.x ?? 0);
    for (const control of [header, saved]) {
      await control.focus();
      await page.keyboard.press('ArrowDown');
      const popup = page.getByRole('listbox');
      await expect(popup).toBeVisible();
      const bounds = await popup.evaluate((el) => {
        const rect = el.getBoundingClientRect();
        return {
          x: rect.x,
          y: rect.y,
          right: rect.right,
          bottom: rect.bottom,
          visibleOnTop: el.contains(
            document.elementFromPoint(rect.x + 12, rect.y + 12),
          ),
          overflow: document.documentElement.scrollWidth > innerWidth,
        };
      });
      expect(bounds.x).toBeGreaterThanOrEqual(0);
      expect(bounds.right).toBeLessThanOrEqual(width);
      expect(bounds.y).toBeGreaterThanOrEqual(0);
      expect(bounds.bottom).toBeLessThanOrEqual(900);
      expect(bounds.visibleOnTop).toBe(true);
      expect(bounds.overflow).toBe(false);
      boxes.push({
        width,
        control: await control.getAttribute('aria-label'),
        ...bounds,
      });
      await page.screenshot({
        path: testInfo.outputPath(
          `dropdown-${width}-${control === header ? 'header' : 'history'}.png`,
        ),
      });
      await page.keyboard.press('Escape');
      await expect(control).toBeFocused();
      await expect(popup).toHaveCount(0);
    }
    await expect(saved).toHaveJSProperty('value', detail.job.id);
    expect((await saved.boundingBox())?.height).toBe(38);
  }
  await header.focus();
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('End');
  await page.keyboard.press('Enter');
  await expect(header).toHaveJSProperty('value', 'second-registered-dataset');
  const operation = page.getByRole('combobox', {
    name: 'Select processing operation',
  });
  await operation.click();
  await expect(page.getByRole('option')).toHaveCount(1);
  await expect(page.getByRole('option')).toHaveText('Resize + Gray');
  await testInfo.attach('dropdown-layout', {
    body: JSON.stringify(boxes, null, 2),
    contentType: 'application/json',
  });
});

test('keeps reference geometry, truthful CPU scaling, shared controls and narrow layouts', async ({
  page,
}) => {
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/datasets') {
      await route.fulfill({
        json: [
          dataset,
          { ...dataset, dataset_id: 'another-registered-dataset' },
        ],
      });
    } else if (path === '/api/benchmarks') {
      await route.fulfill({
        json: Array.from({ length: 5 }, (_, index) => ({
          job: {
            ...detail.job,
            id: `00000000-0000-4000-8000-00000000000${index + 1}`,
          },
          summaries: detail.result?.summaries,
        })),
      });
    } else if (path.startsWith('/api/benchmarks/')) {
      await route.fulfill({ json: detail });
    } else await route.continue();
  });
  await page.setViewportSize({ width: 1440, height: 1080 });
  await page.goto('/');
  await expect(page.locator('canvas')).toHaveCount(2);
  await expect(page.locator('.sidebar-footer')).toContainText('API online');
  await expect(page.locator('.sidebar-footer')).not.toContainText(
    'registered datasets',
  );
  await expect(page.locator('.sidebar-footer')).not.toContainText(
    'Liveness only',
  );
  await expect(page.locator('.sidebar-footer')).not.toContainText('Recovering');
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  const dimensions = await page.evaluate(() => {
    const rect = (selector: string) => {
      const element = document.querySelector(selector);
      if (!element) throw new Error(`Missing ${selector}`);
      const { x, y, width, height } = element.getBoundingClientRect();
      return { x, y, width, height };
    };
    return {
      kpi: rect('.kpi'),
      chart: rect('.chart-card'),
      history: rect('.history-card'),
      quick: rect('.quick-card'),
      root: getComputedStyle(document.documentElement).fontSize,
    };
  });
  expect(dimensions.root).toBe('14px');
  expect(dimensions.kpi).toEqual({
    x: 264,
    y: 116,
    width: 274.75,
    height: 146,
  });
  expect(dimensions.chart.x).toBe(264);
  expect(dimensions.chart.y).toBe(279);
  expect(dimensions.chart.height).toBeCloseTo(356.25, 0);
  expect(dimensions.history.y).toBeCloseTo(652.25, 0);
  expect(dimensions.quick.height).toBe(dimensions.history.height);
  await expect(page.locator('.recent-table th')).toHaveText([
    'Run',
    'Operation',
    'Workers',
    'Time',
    'Speedup',
    'Status',
  ]);
  await page.getByRole('button', { name: '8 workers', exact: true }).click();
  await expect(page.locator('.cpu-footer')).toContainText('560% CPU');
  const header = page.getByRole('combobox', {
    name: 'Select benchmark dataset',
  });
  await header.click();
  await page
    .getByRole('option', { name: 'another-registered-dataset', exact: true })
    .click();
  await expect(
    page.getByRole('combobox', { name: 'Dataset', exact: true }),
  ).toHaveJSProperty('value', 'another-registered-dataset');
  for (const width of [320, 375, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: 1080 });
    for (const view of [
      'overview',
      'experiments',
      'compare',
      'history',
      'api',
    ]) {
      await page.goto(`/#${view}`);
      await expect(page.locator('h1')).toBeVisible();
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      );
      expect(overflow, `${view} at ${width}px`).toBe(false);
    }
  }
});

test('keeps outage notifications quiet and verifies retained jobs separately from connectivity', async ({
  page,
}, testInfo) => {
  let online = false;
  let verifiedJob = false;
  let jobRequests = 0;
  await page.setViewportSize({ width: 375, height: 812 });
  await page.addInitScript(
    (id) =>
      sessionStorage.setItem('tachyron.active.v1', JSON.stringify({ id })),
    detail.job.id,
  );
  await page.route('**/health', async (route) => {
    await route.fulfill(
      online
        ? { json: { status: 'ok' } }
        : { status: 503, body: 'private failure' },
    );
  });
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.startsWith('/api/jobs/')) jobRequests++;
    if (!online || (path.startsWith('/api/jobs/') && !verifiedJob)) {
      await route.fulfill({ status: 503, body: 'private failure' });
      return;
    }
    if (path === '/api/datasets') await route.fulfill({ json: [dataset] });
    else if (path === '/api/benchmarks') await route.fulfill({ json: [] });
    else await route.fulfill({ json: { ...detail.job, state: 'RUNNING' } });
  });
  await page.goto('/#experiments');
  const footer = page.locator('.sidebar-footer');
  await expect(footer).toContainText('API offline');
  await expect(footer).not.toContainText('Recovering');
  await expect(page.locator('.activity-dot')).toHaveCount(0);
  await expect(
    page.getByRole('heading', { name: 'Unable to reach the local API' }),
  ).toHaveCount(1);
  const toast = await page.locator('.api-toast').boundingBox();
  expect(toast?.x).toBeGreaterThanOrEqual(0);
  expect((toast?.x ?? 0) + (toast?.width ?? 0)).toBeLessThanOrEqual(375);
  await expect(page.getByRole('button', { name: 'Details' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Dismiss' }).focus();
  await page.keyboard.press('Enter');
  await expect(
    page.getByRole('button', { name: 'Recheck API connection' }),
  ).toBeFocused();
  await page.getByRole('button', { name: 'Recheck API connection' }).click();
  await expect(footer).toContainText('API offline');
  const attempts = jobRequests;
  await expect.poll(() => jobRequests).toBeGreaterThan(attempts);
  await expect(
    page.getByRole('heading', { name: 'Unable to reach the local API' }),
  ).toHaveCount(0);
  await expect(page.getByText('private failure', { exact: true })).toHaveCount(
    0,
  );
  await page.screenshot({
    path: testInfo.outputPath('offline-retained-job.png'),
    fullPage: true,
  });
  online = true;
  await page.getByRole('button', { name: 'Recheck API connection' }).click();
  await expect(footer).toContainText('API online');
  await expect(
    page.getByRole('heading', { name: 'Connection restored' }),
  ).toBeVisible();
  await expect(page.getByText('UNVERIFIED', { exact: true })).toBeVisible();
  await expect(page.locator('.activity-dot')).toHaveCount(0);
  await page.screenshot({
    path: testInfo.outputPath('online-job-unverified.png'),
    fullPage: true,
  });
  verifiedJob = true;
  await page.getByRole('button', { name: 'Resume status polling' }).click();
  await expect(page.getByText('RUNNING', { exact: true })).toBeVisible();
  await expect(page.locator('.activity-dot')).toHaveCount(1);
  online = false;
  await page.getByRole('button', { name: 'Recheck API connection' }).click();
  await expect(footer).toContainText('API offline');
  await expect(
    page.getByText(/Status unverified · last confirmed RUNNING/),
  ).toBeVisible();
  await expect(page.locator('.activity-dot')).toHaveCount(0);
  expect(
    await page.evaluate(() =>
      JSON.parse(sessionStorage.getItem('tachyron.active.v1') ?? '{}'),
    ),
  ).toEqual({ id: detail.job.id });
});

test('retries slowly and repeatedly without moving, remounting or clearing the dashboard', async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.addInitScript(() => {
    HTMLCanvasElement.prototype.getContext = new Proxy(
      HTMLCanvasElement.prototype.getContext,
      {
        apply(target, canvas: HTMLCanvasElement, args: [string, unknown?]) {
          if (args[0] === '2d')
            canvas.dataset.contextReads = String(
              Number(canvas.dataset.contextReads ?? 0) + 1,
            );
          return target.call(canvas, ...args);
        },
      },
    );
  });
  let online = true;
  let release: (() => void) | undefined;
  let gate = Promise.resolve();
  const requests: string[] = [];
  await page.route('**/health', async (route) => {
    requests.push('/health');
    await gate;
    await route.fulfill(
      online
        ? { json: { status: 'ok' } }
        : { status: 503, body: 'unavailable' },
    );
  });
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    requests.push(path);
    await gate;
    if (!online) await route.fulfill({ status: 503, body: 'unavailable' });
    else if (path === '/api/datasets') await route.fulfill({ json: [dataset] });
    else if (path === '/api/benchmarks')
      await route.fulfill({
        json: [{ job: detail.job, summaries: detail.result?.summaries }],
      });
    else await route.fulfill({ json: detail });
  });
  await page.goto('/');
  await expect(page.locator('canvas')).toHaveCount(2);
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  await page.getByRole('button', { name: '8 workers', exact: true }).click();
  online = false;
  await page.getByRole('button', { name: 'Recheck API connection' }).click();
  const failure = page.getByRole('heading', {
    name: 'Unable to reach the local API',
  });
  await expect(failure).toBeVisible();
  await page.evaluate(() => scrollTo(0, 200));
  const canvases = await page.locator('canvas').elementHandles();
  const snapshot = () =>
    page.evaluate(() => ({
      scrollY,
      cards: [
        ...document.querySelectorAll(
          '.kpi,.chart-card,.history-card,.quick-card',
        ),
      ].map((el) => {
        const r = el.getBoundingClientRect();
        return { x: r.x, y: r.y, width: r.width, height: r.height };
      }),
      contexts: [...document.querySelectorAll('canvas')].map(
        (el) => el.dataset.contextReads,
      ),
      selected: document.querySelector(
        '.worker-selector button[aria-pressed="true"]',
      )?.textContent,
      dataset: document.querySelector<HTMLButtonElement>(
        '[aria-label="Select benchmark dataset"]',
      )?.value,
      result: document.querySelector('.kpi-number')?.textContent,
    }));
  const before = await snapshot();
  const measurements = [{ stage: 'before', ...before }];
  for (let attempt = 0; attempt < 2; attempt++) {
    requests.length = 0;
    gate = new Promise((resolve) => {
      release = resolve;
    });
    const retry = page.getByRole('button', { name: 'Retry', exact: true });
    await retry.focus();
    await page.keyboard.press('Enter');
    await expect(
      page.getByRole('button', { name: 'Checking…', exact: true }),
    ).toHaveAttribute('aria-disabled', 'true');
    await expect.poll(() => requests.length).toBe(1);
    await page.locator('.toast-retry').evaluate((button) => {
      if (!(button instanceof HTMLButtonElement))
        throw new Error('Expected retry button');
      button.click();
      button.click();
    });
    expect(requests).toEqual(['/health']);
    expect(await snapshot()).toEqual(before);
    await page.screenshot({
      path: testInfo.outputPath(`retry-${attempt}-checking.png`),
    });
    release?.();
    await expect(retry).toBeEnabled();
    await expect(retry).toBeFocused();
    expect(await snapshot()).toEqual(before);
    await expect(failure).toHaveCount(1);
    measurements.push({ stage: `failure-${attempt}`, ...(await snapshot()) });
  }
  requests.length = 0;
  gate = new Promise((resolve) => {
    release = resolve;
  });
  await page.getByRole('button', { name: 'Retry', exact: true }).click();
  await expect.poll(() => requests.length).toBe(1);
  online = true;
  release?.();
  gate = Promise.resolve();
  await expect(
    page.getByRole('heading', { name: 'Connection restored' }),
  ).toBeVisible();
  await expect.poll(() => requests.length).toBe(4);
  expect(requests.filter((path) => path === '/health')).toHaveLength(1);
  expect(requests.filter((path) => path === '/api/datasets')).toHaveLength(1);
  expect(requests.filter((path) => path === '/api/benchmarks')).toHaveLength(1);
  expect(
    requests.filter((path) => path.startsWith('/api/benchmarks/')),
  ).toHaveLength(1);
  expect(await snapshot()).toEqual(before);
  await expect(
    page.getByRole('button', { name: 'Dismiss', exact: true }),
  ).toBeFocused();
  for (const canvas of canvases)
    expect(await canvas.evaluate((el) => el.isConnected)).toBe(true);
  measurements.push({ stage: 'reconnected', ...(await snapshot()) });
  await page.screenshot({ path: testInfo.outputPath('retry-reconnected.png') });
  await testInfo.attach('retry-stability', {
    body: JSON.stringify(measurements, null, 2),
    contentType: 'application/json',
  });
});
