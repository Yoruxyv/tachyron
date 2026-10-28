import { expect, test } from '@playwright/test';

import { dataset, detail, request } from '../tests/fixtures';

test('troubleshoots in a native modal without changing the dashboard, scroll, charts or selection', async ({
  page,
  context,
}, testInfo) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
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
  let checks = 0;
  let gate = Promise.resolve();
  let release: (() => void) | undefined;
  await page.route('**/health', async (route) => {
    checks++;
    await gate;
    await route.fulfill(
      online
        ? { json: { status: 'ok' } }
        : { status: 503, body: 'unavailable' },
    );
  });
  const secondId = '00000000-0000-4000-8000-000000000002';
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/datasets') await route.fulfill({ json: [dataset] });
    else if (path === '/api/benchmarks')
      await route.fulfill({
        json: [detail.job.id, secondId].map((id) => ({
          job: { ...detail.job, id },
          summaries: detail.result?.summaries,
        })),
      });
    else
      await route.fulfill({
        json: { ...detail, job: { ...detail.job, id: path.split('/').at(-1) } },
      });
  });
  await page.goto('/#history');
  await expect(page.locator('canvas')).toHaveCount(6);
  await page.evaluate(async () => {
    await document.fonts.ready;
    document.documentElement.style.scrollbarGutter = 'stable';
  });
  await page.getByRole('combobox', { name: 'Saved comparison' }).click();
  await page.getByRole('option').last().click();
  await expect(
    page.getByRole('combobox', { name: 'Saved comparison' }),
  ).toHaveJSProperty('value', secondId);
  await page.getByRole('button', { name: '8 workers', exact: true }).click();
  await page
    .getByRole('searchbox', { name: 'Search runs' })
    .fill(dataset.dataset_id);
  online = false;
  await page.getByRole('button', { name: 'Recheck API connection' }).click();
  const trigger = page.getByRole('button', {
    name: 'Troubleshoot',
    exact: true,
  });
  await expect(trigger).toBeVisible();
  await page.getByRole('button', { name: 'Dismiss', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.evaluate(async () => {
    scrollTo(0, 250);
    await new Promise(requestAnimationFrame);
    await new Promise(requestAnimationFrame);
  });
  const canvases = await page.locator('canvas').elementHandles();
  const snapshot = () =>
    page.evaluate(() => ({
      url: location.href,
      scrollY,
      width: document.documentElement.clientWidth,
      selected: document.querySelector<HTMLButtonElement>(
        '[aria-label="Saved comparison"]',
      )?.value,
      worker: document.querySelector('.worker-selector [aria-pressed="true"]')
        ?.textContent,
      search:
        document.querySelector<HTMLInputElement>('#history-search')?.value,
      contexts: [...document.querySelectorAll('canvas')].map(
        (el) => el.dataset.contextReads,
      ),
      cards: [
        ...document.querySelectorAll(
          '.history-toolbar,.history-card,.history-inspector,.chart-card',
        ),
      ].map((el) => {
        const r = el.getBoundingClientRect();
        return { x: r.x, y: r.y, width: r.width, height: r.height };
      }),
      recovery: sessionStorage.getItem('tachyron.active.v1'),
    }));
  const before = await snapshot();
  await trigger.focus();
  await page.keyboard.press('Enter');
  const dialog = page.getByRole('dialog', {
    name: 'Connection troubleshooting',
  });
  await expect(dialog).toBeVisible();
  await expect(dialog).toHaveAttribute('aria-modal', 'true');
  await expect(
    dialog.getByRole('button', { name: 'Close troubleshooting' }),
  ).toBeFocused();
  expect(await snapshot()).toEqual(before);
  await page.keyboard.press('Shift+Tab');
  await expect(
    dialog.getByRole('button', { name: 'Retry connection' }),
  ).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(
    dialog.getByRole('button', { name: 'Close troubleshooting' }),
  ).toBeFocused();
  await page.keyboard.press('Control+k');
  expect(page.url()).toBe(before.url);
  await page.mouse.move(5, 5);
  await page.mouse.wheel(0, 400);
  await page.evaluate(async () => {
    await new Promise(requestAnimationFrame);
    await new Promise(requestAnimationFrame);
  });
  expect(await snapshot()).toEqual(before);
  await dialog.getByRole('button', { name: 'Copy', exact: true }).click();
  await expect(dialog.getByText('Command copied.')).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(
    'uv run --locked --project apps/server uvicorn tachyron.api:app --host 127.0.0.1 --port 8000',
  );
  await page.screenshot({
    path: testInfo.outputPath('modal-offline-desktop.png'),
  });
  gate = new Promise((resolve) => {
    release = resolve;
  });
  const previousChecks = checks;
  await dialog.getByRole('button', { name: 'Retry connection' }).click();
  const checking = dialog.getByRole('button', {
    name: 'Checking…',
    exact: true,
  });
  await expect(checking).toHaveAttribute('aria-disabled', 'true');
  await checking.evaluate((button) => {
    if (!(button instanceof HTMLButtonElement))
      throw new Error('Missing retry button');
    button.click();
    button.click();
  });
  expect(checks).toBe(previousChecks + 1);
  expect(await snapshot()).toEqual(before);
  await page.screenshot({ path: testInfo.outputPath('modal-checking.png') });
  release?.();
  gate = Promise.resolve();
  await expect(dialog.getByText('API offline', { exact: true })).toBeVisible();
  await expect(
    dialog.getByRole('button', { name: 'Retry connection' }),
  ).toBeFocused();
  expect(await snapshot()).toEqual(before);
  await page.screenshot({
    path: testInfo.outputPath('modal-failed-retry.png'),
  });
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
  expect(await snapshot()).toEqual(before);
  expect(
    await page.evaluate(() => ({
      overflow: document.documentElement.style.overflow,
      gutter: document.documentElement.style.scrollbarGutter,
    })),
  ).toEqual({ overflow: '', gutter: 'stable' });
  await trigger.click();
  await page.mouse.click(5, 5);
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
  expect(await snapshot()).toEqual(before);
  await trigger.click();
  online = true;
  await dialog.getByRole('button', { name: 'Retry connection' }).click();
  await expect(dialog.getByText('API online', { exact: true })).toBeVisible();
  expect(await snapshot()).toEqual(before);
  await page.screenshot({ path: testInfo.outputPath('modal-connected.png') });
  await dialog.getByRole('button', { name: 'Close', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Recheck API connection' }),
  ).toBeFocused();
  expect(await snapshot()).toEqual(before);
  for (const canvas of canvases)
    expect(await canvas.evaluate((el) => el.isConnected)).toBe(true);
  for (const width of [375, 320]) {
    await page.setViewportSize({ width, height: 812 });
    await page.evaluate(() => scrollTo(0, 0));
    online = false;
    await page.getByRole('button', { name: 'Recheck API connection' }).click();
    await trigger.click();
    const bounds = await dialog.boundingBox();
    expect(bounds?.x).toBeGreaterThanOrEqual(15);
    expect((bounds?.x ?? 0) + (bounds?.width ?? 0)).toBeLessThanOrEqual(
      width - 15,
    );
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
    ).toBe(false);
    await page.screenshot({ path: testInfo.outputPath(`modal-${width}.png`) });
    await page.keyboard.press('Escape');
    await expect(trigger).toBeFocused();
    expect(new URL(page.url()).hash).toBe('#history');
  }
  await testInfo.attach('modal-dashboard-preservation', {
    body: JSON.stringify({ before, checks }, null, 2),
    contentType: 'application/json',
  });
});

test('does not submit or recover a retained unconfirmed job when checking connectivity in the modal', async ({
  page,
}) => {
  const retained = { key: 'modal-retained-key', request };
  await page.addInitScript(
    (value) =>
      sessionStorage.setItem('tachyron.active.v1', JSON.stringify(value)),
    retained,
  );
  let online = false;
  let submissions = 0;
  await page.route('**/health', (route) =>
    route.fulfill(
      online
        ? { json: { status: 'ok' } }
        : { status: 503, body: 'unavailable' },
    ),
  );
  await page.route('**/api/**', (route) => {
    if (route.request().method() === 'POST') submissions++;
    return route.fulfill({ status: 503, body: 'unavailable' });
  });
  await page.goto('/#experiments');
  await page.getByRole('button', { name: 'Troubleshoot' }).click();
  const dialog = page.getByRole('dialog');
  online = true;
  await dialog.getByRole('button', { name: 'Retry connection' }).click();
  await expect(dialog.getByText('API online', { exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  expect(
    await page.evaluate(() =>
      JSON.parse(sessionStorage.getItem('tachyron.active.v1') ?? '{}'),
    ),
  ).toEqual(retained);
  expect(submissions).toBe(0);
  await expect(page.locator('.activity-dot')).toHaveCount(0);
  await expect(
    page.getByText(
      /Submission acceptance is unconfirmed\. Retry the same submission from the form/,
    ),
  ).toBeVisible();
});

test('matches the reference logo typography and desktop lockup geometry', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 1080 });
  await page.goto('/');
  const logo = await page.locator('.brand-block').evaluate(async (el) => {
    await document.fonts.ready;
    const wordmark = el.querySelector('strong'),
      subtitle = el.querySelector('small'),
      copy = el.querySelector('.brand-copy'),
      emblem = el.querySelector('.brand-emblem');
    if (!wordmark || !subtitle || !copy || !emblem)
      throw new Error('Incomplete logo lockup');
    const word = getComputedStyle(wordmark),
      sub = getComputedStyle(subtitle),
      rect = el.getBoundingClientRect(),
      symbol = emblem.getBoundingClientRect();
    return {
      word: {
        size: word.fontSize,
        weight: word.fontWeight,
        lineHeight: word.lineHeight,
        spacing: word.letterSpacing,
        color: word.color,
      },
      subtitle: {
        size: sub.fontSize,
        weight: sub.fontWeight,
        lineHeight: sub.lineHeight,
        spacing: sub.letterSpacing,
        color: sub.color,
      },
      gap: getComputedStyle(copy).gap,
      container: {
        x: rect.x,
        y: rect.y,
        width: rect.width,
        height: rect.height,
      },
      emblem: {
        x: symbol.x,
        y: symbol.y,
        width: symbol.width,
        height: symbol.height,
      },
    };
  });
  expect(logo).toEqual({
    word: {
      size: '20px',
      weight: '710',
      lineHeight: '22.4px',
      spacing: '-1.3px',
      color: 'rgb(245, 246, 248)',
    },
    subtitle: {
      size: '9px',
      weight: '520',
      lineHeight: '10.8px',
      spacing: '1.08px',
      color: 'rgb(142, 149, 159)',
    },
    gap: '3px',
    container: { x: 18, y: 30, width: 201, height: 82 },
    emblem: { x: 21, y: 30, width: 38, height: 38 },
  });
});
