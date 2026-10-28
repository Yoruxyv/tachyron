import { expect, test } from '@playwright/test';

test('runs the real tiny 1/2/4/8-worker comparison and restores saved evidence', async ({
  page,
}, testInfo) => {
  await page.goto('/#experiments');
  await expect(page.getByLabel('Dataset', { exact: true })).toHaveValue(
    'browser-test',
  );
  await page.getByLabel('Width (px)').fill('4');
  await page.getByLabel('Height (px)').fill('3');
  await page.getByLabel('Tiny functional test', { exact: false }).check();
  await page.getByLabel('Measured repetitions per worker').fill('1');
  await page.getByRole('button', { name: /Run experiment/ }).click();
  await expect(page.getByRole('progressbar')).toBeVisible();
  await expect(page.getByText('COMPLETED', { exact: true })).toBeVisible({
    timeout: 90000,
  });
  await page.getByRole('link', { name: 'Compare', exact: true }).click();
  await expect(page.getByRole('table')).toBeVisible();
  await expect(page.locator('canvas')).toHaveCount(6);
  await expect(
    page.getByText('CPU (% of 1 core)', { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(/Tiny functional test\. These timings/),
  ).toBeVisible();
  await page.getByRole('button', { name: '8 workers', exact: true }).click();
  await expect(
    page.getByRole('button', { name: '8 workers', exact: true }),
  ).toHaveAttribute('aria-pressed', 'true');
  await page.reload();
  await expect(page.getByRole('table')).toBeVisible();
  await expect(page.locator('canvas')).toHaveCount(6);
  await expect(page.getByText('Loading charts…')).toHaveCount(0);
  await page.screenshot({
    path: testInfo.outputPath('real-tiny-comparison.png'),
    fullPage: true,
  });
  await testInfo.attach('capture-context', {
    body: JSON.stringify(
      {
        captured_at: new Date().toISOString(),
        state:
          'Completed real tiny functional benchmark; generated eight-image test-only dataset, 4x3 output, one repetition, not performance evidence.',
      },
      null,
      2,
    ),
    contentType: 'application/json',
  });
  await page.getByRole('link', { name: 'Overview', exact: true }).click();
  await expect(page.locator('canvas')).toHaveCount(2);
  await page.screenshot({
    path: testInfo.outputPath('real-tiny-overview.png'),
    fullPage: true,
  });
  await page.setViewportSize({ width: 375, height: 812 });
  await expect(page.getByRole('button', { name: /Navigation/ })).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth,
  );
  expect(overflow).toBe(false);
  await page.getByRole('button', { name: /Navigation/ }).click();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('button', { name: /Navigation/ })).toBeFocused();
  await page.getByRole('button', { name: /Navigation/ }).click();
  await page.getByRole('link', { name: 'History', exact: true }).click();
  await expect(
    page.getByRole('button', { name: /browser-test/ }).first(),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath('real-tiny-mobile-history.png'),
    fullPage: true,
  });
});
