import { execFile, spawn } from 'node:child_process';
import { once } from 'node:events';
import { createServer } from 'node:net';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium } from '@playwright/test';
import { build, preview } from 'vite';
import {
  LIGHTHOUSE_BUDGET,
  main as checkBudget,
} from './check-lighthouse-budget.js';

const frontendDirectory = resolve(
  dirname(fileURLToPath(import.meta.url)),
  '..',
);
const repositoryDirectory = resolve(frontendDirectory, '../..');
const apiUrl = 'http://127.0.0.1:8001';
const previewUrl = 'http://127.0.0.1:4173/';
const execute = promisify(execFile);

/**
 * Wait for the owned API, with bounded requests and early process-failure detection.
 * @param {import('node:child_process').ChildProcess} child Owned API process.
 * @param {AbortSignal} signal Cancels startup on interrupt.
 * @returns {Promise<void>} Resolves after a valid liveness response.
 * @throws {Error} Startup fails or does not complete in 15 seconds.
 */
async function waitForApi(child, signal) {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    if (child.exitCode !== null || child.signalCode !== null)
      throw new Error('The isolated audit API exited during startup.');
    signal.throwIfAborted();
    try {
      const response = await fetch(`${apiUrl}/health`, {
        signal: AbortSignal.any([signal, AbortSignal.timeout(400)]),
      });
      if (response.ok) return;
    } catch (error) {
      signal.throwIfAborted();
      if (!(error instanceof Error)) throw error;
    }
    await delay(100, undefined, { signal });
  }
  throw new Error(
    'The isolated audit API did not start. Port 8001 must be free.',
  );
}

/**
 * Close stdin for graceful lifespan cleanup, then bound a failed shutdown.
 * @param {import('node:child_process').ChildProcess} child Owned API process.
 * @returns {Promise<void>} Resolves when the process exits.
 * @throws {Error} The process tree cannot be stopped after a five-second grace period.
 */
async function stopApi(child) {
  if (!child.pid || child.exitCode !== null || child.signalCode !== null)
    return;
  const exited = once(child, 'exit');
  child.stdin?.end();
  const stopped = await Promise.race([
    exited.then(() => true),
    delay(5000).then(() => false),
  ]);
  if (stopped) return;
  if (process.platform === 'win32')
    await execute('taskkill', ['/pid', String(child.pid), '/t', '/f'], {
      timeout: 5000,
      windowsHide: true,
    });
  else child.kill('SIGTERM');
  await Promise.race([
    exited,
    delay(3000).then(() => {
      throw new Error('Audit API shutdown timed out.');
    }),
  ]);
}

/**
 * Audit the shipped production build against an empty, real, isolated API.
 *
 * Five desktop and five mobile runs use fresh API history and no registered
 * datasets. No benchmark is submitted. Reports are retained in a unique directory.
 * All owned resources are closed after startup failures, audit failures, or signals.
 *
 * @returns {Promise<number>} Zero for valid advisory evidence at any score.
 * @throws {Error} Build, startup, browser, audit, report validation, or cleanup fails.
 * @example
 * // After npm ci, backend uv sync, and npx playwright install chromium:
 * // npm run lighthouse
 */
export async function runAudits() {
  const controller = new AbortController();
  const interrupt = () =>
    controller.abort(new Error('Lighthouse interrupted.'));
  process.once('SIGINT', interrupt);
  process.once('SIGTERM', interrupt);
  /** @type {import('node:child_process').ChildProcess | undefined} */
  let api;
  /** @type {import('vite').PreviewServer | undefined} */
  let server;
  /** @type {import('@playwright/test').Browser | undefined} */
  let chrome;
  const directory = resolve(
    frontendDirectory,
    'lighthouse-reports',
    new Date().toISOString().replaceAll(':', '-').replace('.', '-'),
  );
  let status = 1;
  let cleanupFailed = false;
  try {
    mkdirSync(directory, { recursive: true });
    await execute(
      process.execPath,
      [
        resolve(frontendDirectory, 'node_modules/typescript/bin/tsc'),
        '--noEmit',
      ],
      {
        cwd: frontendDirectory,
        timeout: 60000,
        signal: controller.signal,
        windowsHide: true,
      },
    );
    await build({ root: frontendDirectory });
    controller.signal.throwIfAborted();
    // Refuse to use an unrelated service that happens to occupy the audit port.
    const portProbe = await import('node:net');
    const probe = portProbe.createServer();
    probe.listen(8001, '127.0.0.1');
    await once(probe, 'listening');
    await new Promise((done, reject) =>
      probe.close((error) => (error ? reject(error) : done(undefined))),
    );
    api = spawn(
      'uv',
      [
        'run',
        '--locked',
        '--project',
        'apps/server',
        'python',
        'apps/web/scripts/serve-test-api.py',
        '--stdio-shutdown',
      ],
      {
        cwd: repositoryDirectory,
        stdio: ['pipe', 'inherit', 'inherit'],
        windowsHide: true,
      },
    );
    await once(api, 'spawn');
    await waitForApi(api, controller.signal);
    server = await preview({
      root: frontendDirectory,
      preview: {
        host: '127.0.0.1',
        port: 4173,
        strictPort: true,
        proxy: { '/api': apiUrl, '/health': apiUrl },
      },
    });
    const debugServer = createServer();
    debugServer.listen(0, '127.0.0.1');
    await once(debugServer, 'listening');
    const address = debugServer.address();
    if (address === null || typeof address === 'string')
      throw new Error('Cannot reserve a browser debugging port.');
    const chromePort = address.port;
    await new Promise((done, reject) =>
      debugServer.close((error) => (error ? reject(error) : done(undefined))),
    );
    chrome = await chromium.launch({
      headless: true,
      args: [`--remote-debugging-port=${chromePort}`],
      ...(process.env.CHROME_PATH
        ? { executablePath: process.env.CHROME_PATH }
        : {}),
      timeout: 15000,
    });
    writeFileSync(
      resolve(directory, 'audit-state.json'),
      JSON.stringify(
        {
          state:
            'Real API with empty registry and fresh temporary SQLite history; no submitted jobs.',
          audited_at: new Date().toISOString(),
          node: process.version,
          platform: process.platform,
          url: previewUrl,
        },
        null,
        2,
      ) + '\n',
    );
    for (const profile of ['desktop', 'mobile']) {
      for (let run = 1; run <= LIGHTHOUSE_BUDGET.runCount; run += 1) {
        controller.signal.throwIfAborted();
        console.log(
          `Lighthouse ${profile} ${run}/${LIGHTHOUSE_BUDGET.runCount}`,
        );
        const args = [
          resolve(frontendDirectory, 'node_modules/lighthouse/cli/index.js'),
          previewUrl,
          '--output=json',
          `--output-path=${resolve(directory, `${profile}-${run}.json`)}`,
          `--port=${chromePort}`,
          '--only-categories=performance,accessibility,best-practices,seo',
          '--max-wait-for-load=45000',
          '--quiet',
        ];
        if (profile === 'desktop') args.push('--preset=desktop');
        await execute(process.execPath, args, {
          cwd: frontendDirectory,
          timeout: 90000,
          signal: controller.signal,
          windowsHide: true,
          maxBuffer: 1024 * 1024,
        });
      }
    }
    console.log(`Reports: ${directory}`);
    status = checkBudget([directory]);
  } finally {
    process.removeListener('SIGINT', interrupt);
    process.removeListener('SIGTERM', interrupt);
    const httpServer = server?.httpServer;
    const cleanup = await Promise.allSettled([
      Promise.resolve().then(() => chrome?.close()),
      httpServer
        ? new Promise((done, reject) => {
            if ('closeAllConnections' in httpServer)
              httpServer.closeAllConnections();
            httpServer.close((error) =>
              error ? reject(error) : done(undefined),
            );
          })
        : undefined,
      api ? stopApi(api) : undefined,
    ]);
    for (const result of cleanup)
      if (result.status === 'rejected')
        console.error('Audit cleanup failed:', result.reason);
    cleanupFailed = cleanup.some((result) => result.status === 'rejected');
  }
  if (cleanupFailed)
    throw new Error('Audit cleanup did not finish successfully.');
  return status;
}

if (import.meta.main) {
  try {
    process.exitCode = await runAudits();
  } catch (error) {
    console.error(
      error instanceof Error ? error.message : 'Lighthouse execution failed.',
    );
    process.exitCode = 1;
  }
}
