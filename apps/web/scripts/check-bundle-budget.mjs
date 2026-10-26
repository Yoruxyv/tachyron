import { readdir, readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { gzipSync } from 'node:zlib';

/**
 * Measure the JavaScript chunks emitted by the current Vite build.
 * @param {string} directory Vite's assets directory.
 */
export async function inspectBundles(
  directory = fileURLToPath(new URL('../dist/assets/', import.meta.url)),
) {
  const entries = await readdir(directory, { withFileTypes: true });
  const names = entries
    .filter((entry) => entry.isFile() && entry.name.endsWith('.js'))
    .map((entry) => entry.name)
    .sort();
  if (names.length === 0) {
    throw new Error('No JavaScript bundles found; run npm run build first.');
  }
  return Promise.all(
    names.map(async (name) => {
      const data = await readFile(join(directory, name));
      return {
        name,
        rawBytes: data.byteLength,
        gzipBytes: gzipSync(data).byteLength,
      };
    }),
  );
}

if (import.meta.main) {
  try {
    const bundles = await inspectBundles();
    console.log(
      'JavaScript bundle baseline (informational; no size thresholds).',
    );
    console.table(
      bundles.map(({ name, rawBytes, gzipBytes }) => ({
        bundle: name,
        'raw KiB': (rawBytes / 1024).toFixed(2),
        'gzip KiB': (gzipBytes / 1024).toFixed(2),
      })),
    );
    console.log(
      JSON.stringify({
        totalRawBytes: bundles.reduce(
          (sum, bundle) => sum + bundle.rawBytes,
          0,
        ),
        totalGzipBytes: bundles.reduce(
          (sum, bundle) => sum + bundle.gzipBytes,
          0,
        ),
      }),
    );
  } catch (error) {
    console.error(
      error instanceof Error ? error.message : 'Bundle inspection failed',
    );
    process.exitCode = 1;
  }
}
