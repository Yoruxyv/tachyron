import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, sep } from 'node:path';

import { afterEach, describe, expect, it } from 'vitest';

import { inspectBundles } from '../scripts/check-bundle-budget.mjs';

const directories: string[] = [];

afterEach(async () => {
  for (const directory of directories.splice(0)) {
    expect(directory.startsWith(`${tmpdir()}${sep}tachyron-bundle-`)).toBe(
      true,
    );
    await rm(directory, { recursive: true, force: true });
  }
});

async function assetsDirectory(): Promise<string> {
  const directory = await mkdtemp(join(tmpdir(), 'tachyron-bundle-'));
  directories.push(directory);
  return directory;
}

describe('bundle size reporting', () => {
  it('reports all JavaScript chunks in stable order without arbitrary budgets', async () => {
    const directory = await assetsDirectory();
    const chunk = 'const greeting = "Selamat pagi 🖼️";\n'.repeat(100);
    await writeFile(join(directory, 'z.js'), chunk);
    await writeFile(join(directory, 'a.js'), chunk);
    await writeFile(join(directory, 'index.css'), 'body {}');
    await writeFile(join(directory, 'index.js.map'), '{}');
    await mkdir(join(directory, 'directory.js'));

    const bundles = await inspectBundles(directory);

    expect(bundles.map((bundle) => bundle.name)).toEqual(['a.js', 'z.js']);
    for (const bundle of bundles) {
      expect(bundle.rawBytes).toBe(Buffer.byteLength(chunk));
      expect(bundle.gzipBytes).toBeGreaterThan(0);
      expect(bundle.gzipBytes).toBeLessThan(bundle.rawBytes);
    }
  });

  it('rejects an existing directory with no bundles', async () => {
    await expect(inspectBundles(await assetsDirectory())).rejects.toThrow(
      /No JavaScript/,
    );
  });

  it('rejects missing build output', async () => {
    await expect(
      inspectBundles(join(await assetsDirectory(), 'missing')),
    ).rejects.toMatchObject({
      code: 'ENOENT',
    });
  });
});
