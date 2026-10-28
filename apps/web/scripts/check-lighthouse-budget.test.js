import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  evaluateProfile,
  formatResult,
  LIGHTHOUSE_BUDGET,
  main,
} from './check-lighthouse-budget.js';

/** @typedef {Partial<{performance: number, accessibility: number, bestPractices: number, seo: number}>} Scores */

/** @param {Scores} scores */
const report = ({
  performance = 100,
  accessibility = 100,
  bestPractices = 100,
  seo = 100,
} = {}) => ({
  categories: {
    performance: { score: performance / 100 },
    accessibility: { score: accessibility / 100 },
    'best-practices': { score: bestPractices / 100 },
    seo: { score: seo / 100 },
  },
});

/** @param {Scores | ((index: number) => Scores)} overrides */
const evaluate = (overrides = {}) =>
  evaluateProfile(
    'Mobile',
    Array.from({ length: 5 }, (_, index) =>
      report(typeof overrides === 'function' ? overrides(index) : overrides),
    ),
  );

/** @type {string[]} */
const directories = [];
afterEach(() => {
  for (const directory of directories.splice(0)) {
    if (!directory.startsWith(join(tmpdir(), 'tachyron-lighthouse-')))
      throw new Error('Unexpected test directory.');
    rmSync(directory, { recursive: true, force: true });
  }
});

describe('Lighthouse advisory evaluation', () => {
  it('retains the reference targets without enforcing them as a quality gate', () => {
    expect(LIGHTHOUSE_BUDGET).toEqual({
      runCount: 5,
      performanceMeanMin: 95,
      performanceRunMin: 90,
      accessibilityMin: 90,
      bestPracticesMin: 90,
      seoMin: 90,
    });
  });

  it('accepts an arithmetic mean of 95', () => {
    const scores = [90, 95, 95, 95, 100];
    const result = evaluate((index) => ({ performance: scores[index] ?? 0 }));
    expect(result.performanceMean).toBe(95);
    expect(result.passed).toBe(true);
  });

  it('reports a mean below 95', () => {
    const scores = [94, 94, 94, 94, 98];
    const result = evaluate((index) => ({ performance: scores[index] ?? 0 }));
    expect(result.performanceMean).toBe(94.8);
    expect(result.failures).toContain(
      'Mobile performance mean 94.8 < required 95',
    );
  });

  it('reports an individual run below 90', () => {
    const scores = [89, 100, 100, 100, 100];
    expect(
      evaluate((index) => ({ performance: scores[index] ?? 0 })).failures,
    ).toContain('Mobile run 1 Performance 89 < individual floor 90');
  });

  it.each([
    ['Accessibility', { accessibility: 89 }],
    ['Best Practices', { bestPractices: 89 }],
    ['SEO', { seo: 89 }],
  ])('reports %s below 90', (label, scores) => {
    expect(
      evaluate(scores).failures.some((failure) =>
        failure.includes(`${label} 89`),
      ),
    ).toBe(true);
  });

  it('formats deterministic scores and custom budget targets accurately', () => {
    const result = evaluateProfile(
      'Desktop',
      Array.from({ length: 5 }, () => report()),
      { ...LIGHTHOUSE_BUDGET, performanceMeanMin: 97 },
    );
    const output = formatResult(result);
    expect(output).toBe(formatResult(result));
    expect(output).toContain('| 1 | 100.0 | 100.0 | 100.0 | 100.0 |');
    expect(output).toContain('reference 97');
    expect(output).toContain('All reference targets met.');
  });

  it('keeps individual failure details below the table', () => {
    expect(formatResult(evaluate({ accessibility: 89 }))).toContain(
      '- Mobile run 1 Accessibility 89 < required 90',
    );
  });

  it.each([
    null,
    {},
    [],
    { categories: {} },
    { runtimeError: { code: 'FAILED' }, ...report() },
    { categories: { ...report().categories, performance: { score: NaN } } },
    {
      categories: { ...report().categories, performance: { score: Infinity } },
    },
    { categories: { ...report().categories, performance: { score: -0.1 } } },
    { categories: { ...report().categories, performance: { score: 1.01 } } },
    { categories: { ...report().categories, seo: { score: null } } },
  ])('rejects incomplete or invalid report %#', (invalid) => {
    expect(() => evaluateProfile('Mobile', Array(5).fill(invalid))).toThrow();
  });

  it('rejects missing runs', () => {
    expect(() => evaluateProfile('Mobile', [report()])).toThrow(
      'requires 5 reports',
    );
  });

  it('returns success for complete reports below the advisory thresholds', () => {
    const directory = mkdtempSync(join(tmpdir(), 'tachyron-lighthouse-'));
    directories.push(directory);
    for (const profile of ['desktop', 'mobile'])
      for (let run = 1; run <= 5; run += 1)
        writeFileSync(
          join(directory, `${profile}-${run}.json`),
          JSON.stringify(report({ performance: 50 })),
        );
    const output = vi.spyOn(console, 'log').mockImplementation(() => {});
    expect(main([directory])).toBe(0);
    expect(output.mock.calls.flat().join(' ')).toContain('non-blocking');
  });

  it('rejects missing and malformed report files', () => {
    const directory = mkdtempSync(join(tmpdir(), 'tachyron-lighthouse-'));
    directories.push(directory);
    vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(main([directory])).toBe(1);
    writeFileSync(join(directory, 'desktop-1.json'), '{bad json');
    expect(main([directory])).toBe(1);
    expect(main([])).toBe(1);
  });
});
