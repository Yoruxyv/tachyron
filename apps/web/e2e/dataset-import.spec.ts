import { expect, test, type Page, type TestInfo } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

// Pillow-generated 4x3 RGB PNG; functional evidence, not a performance workload.
const png = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAQAAAADCAIAAAA7ljmRAAAAEUlEQVR4nGP8z4AATEhsVA4AJnYBBZNezToAAAAASUVORK5CYII=',
  'base64',
);
const jpeg = Buffer.from(
  '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAADAAQDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDi6KKK+ZP3E//Z',
  'base64',
);

async function choose(page: Page, folder: string, another = false) {
  const chooser = page.waitForEvent('filechooser');
  await page
    .getByRole('button', {
      name: another ? 'Choose another folder' : 'Import folder',
      exact: true,
    })
    .click();
  await (await chooser).setFiles(folder);
  await expect(
    page.getByRole('dialog', { name: 'Review dataset folder' }),
  ).toBeVisible();
}

async function capture(page: Page, testInfo: TestInfo, state: string) {
  for (const width of [1280, 375, 320]) {
    await page.setViewportSize({ width, height: 900 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    const dialog = page.getByRole('dialog');
    if (await dialog.count())
      expect(
        await dialog.evaluate(
          (element) => element.scrollWidth <= element.clientWidth,
        ),
      ).toBe(true);
    const confirmation = page.locator('.import-confirmation');
    if (await confirmation.count()) {
      const group = await confirmation.boundingBox();
      const nameField = await page
        .getByRole('textbox', { name: 'Dataset name' })
        .boundingBox();
      const checkbox = await confirmation.getByRole('checkbox').boundingBox();
      const text = await confirmation.locator('span').boundingBox();
      if (!group || !nameField || !checkbox || !text)
        throw new Error('Missing confirmation group');
      expect(Math.abs(group.x - nameField.x)).toBeLessThan(1);
      expect(text.x - checkbox.x - checkbox.width).toBe(9);
      expect(
        Math.abs(checkbox.y + checkbox.height / 2 - text.y - text.height / 2),
      ).toBeLessThan(1);
    }
    await page.screenshot({
      path: testInfo.outputPath(`dataset-import-${state}-${width}.png`),
      fullPage: true,
    });
  }
}

test('opens the native picker directly with a compact responsive row and no modal on cancel', async ({
  page,
}, testInfo) => {
  await page.goto('/#experiments');
  const picker = page.getByRole('button', {
    name: 'Import folder',
    exact: true,
  });
  await expect(picker).toBeEnabled();
  await expect(picker).toHaveAccessibleDescription(
    'PNG, JPG, JPEG only. Files must be valid and uncorrupted.',
  );
  await expect(page.locator('#import-folder')).toHaveAttribute(
    'webkitdirectory',
    '',
  );
  await expect(page.getByRole('dialog')).toHaveCount(0);
  for (const width of [1280, 375, 320]) {
    await page.setViewportSize({ width, height: 900 });
    const row = await page.locator('.import-picker-row').boundingBox();
    const button = await picker.boundingBox();
    const selector = await page
      .getByRole('combobox', { name: 'Dataset', exact: true })
      .boundingBox();
    if (!row || !button || !selector)
      throw new Error('Missing import controls');
    expect(button.width).toBeLessThan(150);
    expect(selector.y).toBeGreaterThanOrEqual(row.y + row.height);
  }
  await capture(page, testInfo, 'initial');
  const chooser = page.waitForEvent('filechooser');
  await picker.focus();
  await picker.press('Enter');
  await (await chooser).element().dispatchEvent('cancel');
  await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('rejects the entire mixed folder, bounds offending paths, and permits valid reselection without an upload', async ({
  page,
}, testInfo) => {
  let posts = 0;
  page.on('request', (request) => {
    if (
      request.method() === 'POST' &&
      new URL(request.url()).pathname === '/api/dataset-imports'
    )
      posts += 1;
  });
  const folder = testInfo.outputPath('mixed');
  await mkdir(path.join(folder, 'nested'), { recursive: true });
  for (let i = 0; i < 5; i += 1)
    await writeFile(path.join(folder, `${i}.png`), png);
  const names = [
    'report.pdf',
    'renamed.jpg',
    '.DS_Store',
    'Thumbs.db',
    'archive.zip',
    'video.mp4',
    'program.exe',
    ...Array.from({ length: 54 }, (_, i) => `notes-${i}.txt`),
  ];
  for (const name of names)
    await writeFile(path.join(folder, 'nested', name), '%PDF-1.7');
  await page.goto('/#experiments');
  const selector = page.getByRole('combobox', { name: 'Dataset', exact: true });
  await expect(selector).not.toHaveJSProperty('value', '');
  const previous = await selector.getAttribute('value');
  await choose(page, folder);
  await expect(
    page.getByRole('heading', { name: 'Unsupported files detected' }),
  ).toBeVisible();
  await expect(page.getByText('61 unsupported files found:')).toBeVisible();
  await expect(page.getByRole('dialog').getByRole('checkbox')).toHaveCount(0);
  await expect(
    page.getByRole('button', { name: /Confirm and import/ }),
  ).toHaveCount(0);
  const list = page.getByRole('region', { name: 'Unsupported folder files' });
  const observed = [...(await list.locator('li > span').allTextContents())];
  await expect(list.getByRole('listitem')).toHaveCount(50);
  await page.getByRole('button', { name: 'Next files' }).click();
  await expect(list.getByRole('listitem')).toHaveCount(11);
  observed.push(...(await list.locator('li > span').allTextContents()));
  for (const name of names)
    expect(observed.some((text) => text.startsWith(`nested/${name}`))).toBe(
      true,
    );
  await capture(page, testInfo, 'rejected');
  expect(posts).toBe(0);
  await expect(
    page.getByRole('combobox', { name: 'Dataset', exact: true }),
  ).toHaveJSProperty('value', previous);
  const valid = testInfo.outputPath('valid');
  await mkdir(valid);
  await writeFile(path.join(valid, 'image.png'), png);
  await choose(page, valid, true);
  await expect(
    page.getByRole('button', { name: 'Confirm and import 1 files' }),
  ).toBeEnabled();
  await expect(
    page.getByRole('heading', { name: 'Unsupported files detected' }),
  ).toHaveCount(0);
  await expect(
    page.getByRole('heading', { name: 'Dataset import needs attention' }),
  ).toHaveCount(0);
  expect(posts).toBe(0);
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(
    page.getByRole('button', { name: 'Import folder', exact: true }),
  ).toBeFocused();
});

test('reviews six real images, requires consent, closes on READY and restores quietly after reload', async ({
  page,
}, testInfo) => {
  await observeWorker(page);
  const posts: string[] = [];
  page.on('request', (request) => {
    if (
      request.method() === 'POST' &&
      new URL(request.url()).pathname === '/api/dataset-imports'
    )
      posts.push(request.url());
  });
  const folder = testInfo.outputPath('images');
  await mkdir(path.join(folder, 'nested'), { recursive: true });
  for (const name of ['a.png', 'b.PNG', 'c.png'])
    await writeFile(path.join(folder, 'nested', name), png);
  for (const name of ['d.jpg', 'e.jpeg', 'f.JPG'])
    await writeFile(path.join(folder, 'nested', name), jpeg);
  await page.goto('/#experiments');
  await choose(page, folder);
  await expect(
    page.getByText(/6 included .*0 explicitly excluded/),
  ).toBeVisible();
  const confirm = page.getByRole('button', {
    name: 'Confirm and import 6 files',
  });
  await confirm.click();
  const acknowledgement = page.getByRole('checkbox', { name: /I confirm/ });
  await expect(acknowledgement).toBeFocused();
  await expect(acknowledgement).toHaveAccessibleDescription(
    'Acknowledge the included images and exclusions before importing.',
  );
  await expect(
    page.getByRole('heading', { name: 'Confirm the workload' }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Dismiss', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: 'Review dataset folder' }),
  ).toBeFocused();
  await page.getByRole('button', { name: 'Choose another folder' }).focus();
  await page.keyboard.press('Tab');
  await expect(
    page.getByRole('textbox', { name: 'Dataset name' }),
  ).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  await expect(
    page.getByRole('button', { name: 'Choose another folder' }),
  ).toBeFocused();
  await choose(page, folder, true);
  await expect(acknowledgement).not.toBeChecked();
  await capture(page, testInfo, 'review');
  await page.setViewportSize({ width: 1280, height: 900 });
  const foreignOrigin = await forgeWindowMessages(page);
  expect(posts).toHaveLength(0);
  await acknowledgement.check();
  await confirm.click();
  await expect(
    page.getByRole('heading', { name: 'Dataset registered', exact: true }),
  ).toBeVisible();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(
    page.getByRole('button', {
      name: /Confirm and import|Check import status/,
    }),
  ).toHaveCount(0);
  const identifier = await page.evaluate(() =>
    sessionStorage.getItem('tachyron.import.v1'),
  );
  expect(identifier).not.toBeNull();
  await expect(
    page.getByRole('combobox', { name: 'Dataset', exact: true }),
  ).toHaveJSProperty('value', `import-${identifier?.replaceAll('-', '')}`);
  expect(posts).toHaveLength(1);
  const nativeMessage: unknown = JSON.parse(
    (await page.locator('html').getAttribute('data-preparation-message')) ??
      '{}',
  );
  expect(nativeMessage).toEqual({
    origin: '',
    sourceIsNull: true,
    trusted: true,
    targetIsWorker: true,
  });
  await testInfo.attach('dedicated-worker-message-boundary', {
    body: JSON.stringify({
      foreignOrigin,
      nativeMessage,
      importPosts: posts.length,
    }),
    contentType: 'application/json',
  });
  await page.reload();
  await expect(
    page.getByRole('button', { name: 'Import folder', exact: true }),
  ).toBeEnabled();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(
    page.getByRole('heading', { name: 'Dataset registered', exact: true }),
  ).toHaveCount(0);
  expect(posts).toHaveLength(1);
  await choose(page, folder);
  await page.getByRole('checkbox', { name: /nested\/a.png/ }).uncheck();
  await expect(
    page.getByText(/5 included .*1 explicitly excluded/),
  ).toBeVisible();
  await acknowledgement.check();
  await page
    .getByRole('button', { name: 'Confirm and import 5 files' })
    .click();
  await expect(
    page.getByRole('heading', { name: 'Dataset registered', exact: true }),
  ).toBeVisible();
  expect(posts).toHaveLength(2);
  expect(
    await page.evaluate(() => sessionStorage.getItem('tachyron.import.v1')),
  ).not.toBe(identifier);
  await testInfo.attach('capture-context', {
    body: JSON.stringify({
      captured_at: new Date().toISOString(),
      widths: [1280, 375, 320],
      workload:
        'Generated tiny PNG/JPEG fixtures; real API validation, immutable registration, repeated import and quiet reload. Functional evidence only.',
    }),
    contentType: 'application/json',
  });
});

test('leaves full corrupt-image validation to the server and retains the previous selection', async ({
  page,
}, testInfo) => {
  const folder = testInfo.outputPath('corrupt');
  await mkdir(folder);
  await writeFile(path.join(folder, 'broken.png'), png.subarray(0, 8));
  await page.goto('/#experiments');
  const selector = page.getByRole('combobox', { name: 'Dataset', exact: true });
  await expect(selector).not.toHaveJSProperty('value', '');
  const previous = await selector.getAttribute('value');
  await choose(page, folder);
  await expect(
    page.getByRole('button', { name: 'Confirm and import 1 files' }),
  ).toBeEnabled();
  await page.getByRole('checkbox', { name: /I confirm/ }).check();
  await page
    .getByRole('button', { name: 'Confirm and import 1 files' })
    .click();
  await expect(page.getByText('Import failed', { exact: true })).toBeVisible();
  await expect(
    page
      .getByText(
        'A selected image cannot be decoded. Review the files and start a new import.',
        { exact: true },
      )
      .first(),
  ).toBeVisible();
  await expect(selector).toHaveJSProperty('value', previous);
  await expect(
    page.getByRole('heading', { name: 'Dataset registered' }),
  ).toHaveCount(0);
});

async function observeWorker(page: Page) {
  await page.addInitScript(() => {
    const NativeWorker = window.Worker;
    window.Worker = class extends NativeWorker {
      constructor(scriptURL: string | URL, options?: WorkerOptions) {
        super(scriptURL, options);
        this.addEventListener('message', (event: MessageEvent<unknown>) => {
          document.documentElement.setAttribute(
            'data-preparation-message',
            JSON.stringify({
              origin: event.origin,
              sourceIsNull: event.source === null,
              trusted: event.isTrusted,
              targetIsWorker: event.target === this,
            }),
          );
        });
      }
    };
  });
}

async function forgeWindowMessages(page: Page) {
  await page.route('https://message-probe.invalid/', (route) =>
    route.fulfill({
      contentType: 'text/html',
      body: `<script>const forge = () => parent.postMessage({kind:'complete', metadata:'{}', digest:'0'.repeat(64)}, '*'); forge(); setInterval(forge, 50);</script>`,
    }),
  );
  return page.evaluate(
    () =>
      new Promise<string>((resolve) => {
        const frame = document.createElement('iframe');
        frame.hidden = true;
        const receive = (event: MessageEvent<unknown>) => {
          if (
            event.source !== frame.contentWindow ||
            event.origin !== 'https://message-probe.invalid'
          )
            return;
          window.removeEventListener('message', receive);
          resolve(event.origin);
        };
        window.addEventListener('message', receive);
        frame.src = 'https://message-probe.invalid/';
        document.body.append(frame);
      }),
  );
}
