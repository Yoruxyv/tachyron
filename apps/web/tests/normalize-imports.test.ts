import { spawnSync } from 'node:child_process';
import {
  copyFileSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

let root: string;

function write(file: string, text: string) {
  const target = join(root, file);
  mkdirSync(dirname(target), { recursive: true });
  writeFileSync(target, text);
}

function run(...flags: string[]) {
  return spawnSync(
    process.execPath,
    [join(root, 'scripts/normalize-imports.mjs'), ...flags],
    { encoding: 'utf8', timeout: 10000 },
  );
}

beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), 'tachyron-imports-'));
  write(
    'tsconfig.json',
    JSON.stringify({
      compilerOptions: {
        module: 'ESNext',
        moduleResolution: 'Bundler',
        paths: { '@/*': ['./src/*'] },
      },
      include: ['src'],
    }),
  );
  write('src/api/value.ts', 'export const value = 1;\n');
  mkdirSync(join(root, 'scripts'));
  copyFileSync(
    resolve('scripts/normalize-imports.mjs'),
    join(root, 'scripts/normalize-imports.mjs'),
  );
  symlinkSync(resolve('node_modules'), join(root, 'node_modules'), 'junction');
});

afterEach(() => {
  if (!root.startsWith(join(tmpdir(), 'tachyron-imports-')))
    throw new Error('Unexpected fixture root.');
  rmSync(root, { recursive: true, force: true });
});

describe('import normalization CLI', () => {
  it('scans supported sources in stable order while excluding generated files and links', () => {
    const original = "import x from '../../api/value';\n";
    for (const file of [
      'src/features/z/use.tsx',
      'src/features/a/use.cts',
      'src/features/m/use.mts',
    ])
      write(file, original);
    for (const directory of [
      '.git',
      '.vite-cache',
      'coverage',
      'dist',
      'node_modules',
    ])
      write(`src/${directory}/broken.ts`, "import x from './missing';\n");
    write('src/ignored.d.ts', "import x from './missing';\n");
    write('src/ignored.js', "import x from './missing';\n");
    write('external/broken.ts', "import x from './missing';\n");
    symlinkSync(join(root, 'external'), join(root, 'src/linked'), 'junction');

    const first = run('--strict');
    expect(first.status).toBe(0);
    expect(first.stderr).toBe('');
    expect(first.stdout).toBe(run('--strict').stdout);
    for (const file of [
      'src/features/a/use.cts',
      'src/features/m/use.mts',
      'src/features/z/use.tsx',
    ])
      expect(first.stdout).toContain(file);
    expect(first.stdout.indexOf('src/features/a/use.cts')).toBeLessThan(
      first.stdout.indexOf('src/features/m/use.mts'),
    );
    expect(first.stdout.indexOf('src/features/m/use.mts')).toBeLessThan(
      first.stdout.indexOf('src/features/z/use.tsx'),
    );
    expect(readFileSync(join(root, 'src/features/z/use.tsx'), 'utf8')).toBe(
      original,
    );
  });

  it('accepts absent scan roots and propagates a source-directory read failure', () => {
    rmSync(join(root, 'src'), { recursive: true });
    write('entry.ts', 'export {};\n');
    write('tsconfig.json', JSON.stringify({ files: ['entry.ts'] }));
    expect(run('--check', '--strict').status).toBe(0);
    write('src', 'A file cannot be traversed as a directory.');
    const failure = run('--write', '--strict');
    expect(failure.status).not.toBe(0);
    expect(failure.stderr).toMatch(/ENOTDIR/);
    expect(readFileSync(join(root, 'src'), 'utf8')).toBe(
      'A file cannot be traversed as a directory.',
    );
  });

  it('previews without writing, applies fixes, and remains idempotent', () => {
    const file = 'src/features/sample/use.ts';
    const original = "import { value } from '../../api/value';\n";
    write(file, original);
    expect(run().status).toBe(0);
    expect(readFileSync(join(root, file), 'utf8')).toBe(original);
    expect(run('--check', '--strict').status).toBe(1);
    expect(run('--write', '--strict').status).toBe(0);
    expect(readFileSync(join(root, file), 'utf8')).toBe(
      "import { value } from '@/api/value';\n",
    );
    expect(run('--check', '--strict').status).toBe(0);
  });

  it('preserves nearby relative imports, valid aliases, external imports, comments, and strings', () => {
    const original =
      "import { value } from './api/value';\nimport x from '@/api/value';\nimport React from 'react';\n// import x from '../../api/value'\nconst text = \"import x from '../../api/value'\";\n";
    write('src/app.ts', original);
    expect(run('--write', '--strict').status).toBe(0);
    expect(readFileSync(join(root, 'src/app.ts'), 'utf8')).toBe(original);
  });

  it('rewrites exports, type imports, dynamic imports and cross-feature imports', () => {
    write('src/features/second/value.ts', 'export const value = 2;\n');
    write(
      'src/features/first/use.ts',
      "export * from '../second/value';\ntype V = import('../../api/value').value;\nconst promise = import('../../api/value');\n",
    );
    expect(run('--write', '--strict').status).toBe(0);
    const result = readFileSync(
      join(root, 'src/features/first/use.ts'),
      'utf8',
    );
    expect(result).toContain("from '@/features/second/value'");
    expect(result).toContain("import('@/api/value')");
    expect(run('--check', '--strict').status).toBe(0);
  });

  it('preserves literal asset suffixes and JSON extensions', () => {
    write('src/assets/data.json', '{}');
    write('src/assets/theme.css', 'body {}');
    write(
      'src/features/sample/use.ts',
      "import css from '../../assets/theme.css?inline';\nimport data from '../../assets/data.json';\n",
    );
    expect(run('--write', '--strict').status).toBe(0);
    expect(
      readFileSync(join(root, 'src/features/sample/use.ts'), 'utf8'),
    ).toContain('@/assets/theme.css?inline');
    expect(
      readFileSync(join(root, 'src/features/sample/use.ts'), 'utf8'),
    ).toContain('@/assets/data.json');
  });

  it('keeps an explicit index target when a sibling module would shadow it', () => {
    write('src/api/index.ts', 'export const value = 2;');
    write('src/api.ts', 'export const value = 3;');
    const original = "import x from '../../api/index';\n";
    write('src/features/sample/use.ts', original);
    expect(run('--write', '--strict').status).toBe(0);
    expect(readFileSync(join(root, 'src/features/sample/use.ts'), 'utf8')).toBe(
      original,
    );
  });

  it('fails strict writes before changing any file when another import is unresolved', () => {
    const original = "import x from '../../api/value';\n";
    write('src/features/sample/a.ts', original);
    write('src/features/sample/z.ts', "import x from './missing';\n");
    const blocked = run('--write', '--strict');
    expect(blocked.status).toBe(2);
    expect(blocked.stderr).toContain('No files were written');
    expect(blocked.stdout).not.toContain('updated complete');
    expect(readFileSync(join(root, 'src/features/sample/a.ts'), 'utf8')).toBe(
      original,
    );
  });

  it('rejects conflicting or unknown options without changes', () => {
    expect(run('--write', '--check').status).toBe(2);
    expect(run('--invalid').status).toBe(2);
    expect(run('--help').status).toBe(0);
  });

  it('does not rewrite a symlink target outside the source namespace', () => {
    write('external/value.ts', 'export const value = 1;\n');
    symlinkSync(join(root, 'external'), join(root, 'src/external'), 'junction');
    const original = "import x from '../../external/value';\n";
    write('src/features/sample/use.ts', original);
    expect(run('--write', '--strict').status).toBe(0);
    expect(readFileSync(join(root, 'src/features/sample/use.ts'), 'utf8')).toBe(
      original,
    );
  });

  it('escapes quotes and template interpolation while preserving literal targets', () => {
    write("src/api/o'clock.ts", 'export const value = 1;\n');
    write('src/api/cost${tier}.ts', 'export const value = 2;\n');
    write(
      'src/features/sample/use.ts',
      "import x from '../../api/o\\'clock';\nconst value = import(`../../api/cost\\${tier}`);\n",
    );
    expect(run('--write', '--strict').status).toBe(0);
    const result = readFileSync(
      join(root, 'src/features/sample/use.ts'),
      'utf8',
    );
    expect(result).toContain("@/api/o\\'clock");
    expect(result).toContain('@/api/cost\\${tier}');
    expect(run('--check', '--strict').status).toBe(0);
  });
});
