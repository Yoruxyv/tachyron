import { expect, test } from '@playwright/test';

import { parseHistoryDetail } from '../src/api/decode';

const wide = process.env.TACHYRON_D3_WIDE_EXAMPLE === '1';
const workerCounts = wide ? [1, 2, 3, 5, 9] : [1, 2];
const dataset = wide ? 'comparison-test' : 'browser-test';

test('runs a custom comparison and opens its saved worker evidence', async ({
  page,
  request,
}, testInfo) => {
  await page.goto('/#experiments');
  await expect(page.getByLabel('Dataset', { exact: true })).toHaveJSProperty(
    'value',
    'browser-test',
  );
  if (wide) {
    await page.getByRole('combobox', { name: 'Dataset', exact: true }).click();
    await page.getByRole('option', { name: /comparison-test/ }).click();
  }
  await expect(page.getByLabel('Dataset', { exact: true })).toHaveJSProperty(
    'value',
    dataset,
  );
  await page.getByLabel('Width (px)').fill('16');
  await page.getByLabel('Height (px)').fill('12');
  await page.getByLabel('Tiny functional test', { exact: false }).check();
  await page.getByLabel('Measured repetitions per worker').fill('1');
  const counts = page.getByRole('textbox', { name: 'Custom worker counts' });
  await counts.fill('1, 1');
  await page.getByRole('button', { name: /Run experiment/ }).click();
  await expect(
    page.getByText(/Enter up to 10 distinct worker counts/),
  ).toBeVisible();
  await counts.fill([...workerCounts].reverse().join(', '));

  const acceptedPromise = page.waitForResponse(
    (response) =>
      response.url().endsWith('/api/jobs') &&
      response.request().method() === 'POST',
  );
  await page.getByRole('button', { name: /Run experiment/ }).click();
  const accepted = await acceptedPromise;
  expect(accepted.status()).toBe(202);
  expect(accepted.request().postDataJSON()).toMatchObject({
    worker_counts: workerCounts,
  });
  const { id } = (await accepted.json()) as { id: string };
  await expect(page.getByText('COMPLETED', { exact: true })).toBeVisible({
    timeout: 90000,
  });

  const saved = await request.get(`/api/benchmarks/${id}`, {
    headers: { 'X-Tachyron-Request': '1' },
  });
  expect(saved.ok()).toBe(true);
  const { job, result } = parseHistoryDetail(await saved.json());
  expect(job.id).toBe(id);
  expect(job.succeeded_images).toBe(job.input_count * workerCounts.length * 2);
  expect(job.failed_images + job.unknown_batches).toBe(0);
  expect(result?.schema_version).toBe(2);
  expect(result?.summaries).toEqual(
    workerCounts.map((workers) =>
      expect.objectContaining({ workers, measured_count: 1, complete: true }),
    ),
  );
  expect(result?.summaries[0]?.speedup).toBe(1);
  await testInfo.attach('real custom benchmark evidence', {
    body: JSON.stringify({ job, result }, null, 2),
    contentType: 'application/json',
  });

  await page.getByRole('link', { name: 'Compare', exact: true }).click();
  await expect(page.getByRole('table')).toBeVisible();
  const resultsLink = page.getByRole('link', {
    name: /View all worker results/,
  });
  await expect(resultsLink).toHaveAttribute(
    'href',
    `#compare/workers?experiment=${id}`,
  );
  if (wide) {
    await expect(
      page.getByText('Showing 4 of 5 measured configurations.'),
    ).toBeVisible();
    await page.getByRole('button', { name: '9 workers', exact: true }).click();
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole('link', { name: 'Overview', exact: true }).click();
  await expect(page.locator('canvas')).toHaveCount(2);
  await expect(
    page.getByText('Mean of 1 measured run', { exact: true }),
  ).toBeVisible();
  if (wide) {
    await expect(
      page.getByText('Showing 4 of 5 measured configurations.'),
    ).toBeVisible();
  }
  await page.screenshot({
    path: testInfo.outputPath('real-custom-highlights.png'),
  });
  await page.getByRole('link', { name: 'Compare', exact: true }).click();
  await expect(page.locator('canvas')).toHaveCount(6);
  await resultsLink.press('Enter');
  const heading = page.getByRole('heading', {
    name: 'All worker results',
    level: 1,
  });
  await expect(heading).toBeFocused();
  await expect(
    page.getByRole('link', { name: 'Compare', exact: true }),
  ).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('canvas')).toHaveCount(1);
  const table = page.getByRole('table', { name: /Measured metrics/ });
  await expect(table.getByRole('rowheader')).toHaveText(
    workerCounts.map((count) => `${count}W`),
  );
  if (wide) await expect(page.getByText(/Inspecting 9W/)).toBeVisible();

  await page.screenshot({
    path: testInfo.outputPath('real-all-worker-results.png'),
    fullPage: true,
  });
  const metric = page.getByRole('combobox', { name: 'Chart metric' });
  await metric.focus();
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Enter');
  await expect(
    page.getByRole('heading', { name: 'CPU utilization' }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Sort by chart metric' }).click();
  await expect(table.getByRole('rowheader')).toHaveCount(workerCounts.length);
  await page.goBack();
  await expect(
    page.getByRole('heading', { name: 'Scaling comparison' }),
  ).toBeVisible();
  if (wide) {
    await expect(
      page.getByRole('button', { name: '9 workers', exact: true }),
    ).toHaveAttribute('aria-pressed', 'true');
  }
  await page.goForward();
  await expect(heading).toBeVisible();
  await page.reload();
  await expect(table.getByRole('rowheader')).toHaveText(
    workerCounts.map((count) => `${count}W`),
  );

  for (const width of [320, 375, 390, 768, 1280, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
      .toBeLessThanOrEqual(width);
    await expect(heading).toBeVisible();
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('canvas')).toHaveCount(1);
  await page.screenshot({
    path: testInfo.outputPath('real-all-worker-results-mobile.png'),
    fullPage: true,
  });

  await page.goto(
    '/#compare/workers?experiment=00000000-0000-4000-8000-000000000099',
  );
  await expect(
    page.getByText('This dataset or experiment is no longer available.'),
  ).toBeVisible();
  await expect(table).toHaveCount(0);
  await page.goto('/#compare/workers?experiment=invalid');
  await expect(
    page.getByText(/Choose a saved benchmark from Compare/),
  ).toBeVisible();
  await expect(table).toHaveCount(0);
});
