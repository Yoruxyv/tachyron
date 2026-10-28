import { appendFileSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

/**
 * Reference score targets for advisory reporting, not repository quality gates.
 * @typedef {{runCount: number, performanceMeanMin: number, performanceRunMin: number, accessibilityMin: number, bestPracticesMin: number, seoMin: number}} Budget
 * @typedef {{run: number, performance: number, accessibility: number, bestPractices: number, seo: number}} ScoreRow
 * @typedef {{profile: string, rows: ScoreRow[], performanceMean: number, failures: string[], passed: boolean, budget: Budget}} ProfileResult
 */
export const LIGHTHOUSE_BUDGET = Object.freeze({
  runCount: 5,
  performanceMeanMin: 95,
  performanceRunMin: 90,
  accessibilityMin: 90,
  bestPracticesMin: 90,
  seoMin: 90,
});

/** @param {unknown} value @returns {value is Record<string, unknown>} */
function isRecord(value) {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * Read a complete category score without accepting NaN or out-of-range values.
 * @param {unknown} report Parsed Lighthouse JSON.
 * @param {string} category Lighthouse category identifier.
 * @returns {number} Score on the 0–100 scale.
 * @throws {Error} The report failed or the category is missing or invalid.
 */
function categoryScore(report, category) {
  if (
    !isRecord(report) ||
    report.runtimeError != null ||
    !isRecord(report.categories)
  ) {
    throw new Error('Incomplete Lighthouse report.');
  }
  const entry = report.categories[category];
  if (
    !isRecord(entry) ||
    typeof entry.score !== 'number' ||
    !Number.isFinite(entry.score) ||
    entry.score < 0 ||
    entry.score > 1
  ) {
    throw new Error(
      `Missing or invalid Lighthouse category score: ${category}`,
    );
  }
  return entry.score * 100;
}

/**
 * Evaluate all runs against the reference targets without enforcing CI policy.
 * @param {string} profile Desktop or Mobile report group.
 * @param {readonly unknown[]} reports Exactly the configured number of parsed reports.
 * @param {Budget} budget Advisory targets.
 * @returns {ProfileResult} Scores and individual/mean shortfalls.
 * @throws {Error} Reports are incomplete, invalid, or incorrectly counted.
 * @example
 * const result = evaluateProfile('Desktop', reports);
 * console.log(result.performanceMean);
 */
export function evaluateProfile(profile, reports, budget = LIGHTHOUSE_BUDGET) {
  if (reports.length !== budget.runCount || budget.runCount < 1) {
    throw new Error(
      `${profile} requires ${budget.runCount} reports; received ${reports.length}.`,
    );
  }
  const rows = reports.map((report, index) => ({
    run: index + 1,
    performance: categoryScore(report, 'performance'),
    accessibility: categoryScore(report, 'accessibility'),
    bestPractices: categoryScore(report, 'best-practices'),
    seo: categoryScore(report, 'seo'),
  }));
  const performanceMean =
    rows.reduce((sum, row) => sum + row.performance, 0) / rows.length;
  const failures = [];
  for (const row of rows) {
    if (row.performance < budget.performanceRunMin)
      failures.push(
        `${profile} run ${row.run} Performance ${row.performance} < individual floor ${budget.performanceRunMin}`,
      );
    if (row.accessibility < budget.accessibilityMin)
      failures.push(
        `${profile} run ${row.run} Accessibility ${row.accessibility} < required ${budget.accessibilityMin}`,
      );
    if (row.bestPractices < budget.bestPracticesMin)
      failures.push(
        `${profile} run ${row.run} Best Practices ${row.bestPractices} < required ${budget.bestPracticesMin}`,
      );
    if (row.seo < budget.seoMin)
      failures.push(
        `${profile} run ${row.run} SEO ${row.seo} < required ${budget.seoMin}`,
      );
  }
  if (performanceMean < budget.performanceMeanMin)
    failures.push(
      `${profile} performance mean ${performanceMean.toFixed(1)} < required ${budget.performanceMeanMin}`,
    );
  return {
    profile,
    rows,
    performanceMean,
    failures,
    passed: failures.length === 0,
    budget,
  };
}

/**
 * Format scores and shortfalls as a deterministic Markdown table.
 * @param {ProfileResult} result Validated profile evaluation.
 * @returns {string} Advisory report suitable for a terminal or Actions summary.
 */
export function formatResult(result) {
  return [
    `### ${result.profile} (advisory)`,
    '',
    '| Run | Performance | Accessibility | Best practices | SEO |',
    '| --- | --- | --- | --- | --- |',
    ...result.rows.map(
      (row) =>
        `| ${row.run} | ${row.performance.toFixed(1)} | ${row.accessibility.toFixed(1)} | ${row.bestPractices.toFixed(1)} | ${row.seo.toFixed(1)} |`,
    ),
    '',
    `Performance mean: ${result.performanceMean.toFixed(1)} (reference ${result.budget.performanceMeanMin}).`,
    result.passed
      ? 'All reference targets met.'
      : 'Reference shortfalls (non-blocking):',
    ...result.failures.map((failure) => `- ${failure}`),
  ].join('\n');
}

/**
 * Read exactly five reports for each browser profile.
 * @param {string} directory Directory printed by the audit runner.
 * @returns {ProfileResult[]} Desktop and mobile results.
 * @throws {Error} A file is missing, malformed, or contains incomplete evidence.
 */
export function checkReportDirectory(directory) {
  return ['Desktop', 'Mobile'].map((profile) =>
    evaluateProfile(
      profile,
      Array.from({ length: LIGHTHOUSE_BUDGET.runCount }, (_, index) => {
        const body = readFileSync(
          resolve(directory, `${profile.toLowerCase()}-${index + 1}.json`),
          'utf8',
        );
        /** @type {unknown} */
        const report = JSON.parse(body);
        return report;
      }),
    ),
  );
}

/**
 * Print advisory results and optionally append an Actions summary.
 * @param {string[]} args Report directory, with no threshold-enforcement option.
 * @returns {number} Zero for valid reports at any score; one for unusable evidence.
 * @example
 * node scripts/check-lighthouse-budget.js lighthouse-reports/2026-10-27T12-00-00-000Z
 */
export function main(args = process.argv.slice(2)) {
  try {
    if (args.length !== 1)
      throw new Error(
        'Supply the report directory printed by npm run lighthouse.',
      );
    const output = [
      '## Tachyron Lighthouse (advisory)',
      '',
      ...checkReportDirectory(resolve(args[0] ?? '')).map(formatResult),
    ].join('\n');
    console.log(output);
    if (process.env.GITHUB_STEP_SUMMARY)
      appendFileSync(process.env.GITHUB_STEP_SUMMARY, `${output}\n`);
    return 0;
  } catch (error) {
    console.error(
      `Tachyron Lighthouse reporting failed: ${error instanceof Error ? error.message : 'Unknown report error'}`,
    );
    return 1;
  }
}

if (import.meta.main) process.exitCode = main();
