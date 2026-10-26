# Tachyron Frontend Agent Instructions

These instructions apply to every file under `apps/web/`. Follow any repository-root `AGENTS.md` as well. Where policies conflict, surface the inconsistency rather than weakening enforcement without review.

## Mission and product truth

Build an accessible, responsive interface for configuring image-processing benchmarks, monitoring jobs, comparing worker counts, and interpreting **real measured results**. Tachyron is a single-host parallel worker experiment, not a production multi-node image-processing cluster.

The frontend is a client of backend truth. It may format, visualize, filter, and derive presentation-only values, but the backend owns official run states, metrics, statistics, worker configuration validation, and dataset identity. Never generate synthetic results in a production dashboard or imply API acceptance means a benchmark finished.

## Engineering decision protocol

1. Read the relevant feature code, contracts, tests, and active phase instructions before editing.
2. Evaluate options against complexity, accessibility, data ownership, lifecycle, API compatibility, and future divergence. Explicitly allow **KEEP / DEFER / REJECT**.
3. Use the smallest maintainable solution; similar-looking components do not automatically need a shared abstraction.
4. Avoid unrelated design-system migrations, decorative dependencies, speculative state management, and premature optimization.
5. Consider loading races, stale responses, cancellation, partial failures, navigation, and regression risks.
6. Demonstrate behavior with tests and complete diff review. Do not suppress lint/type failures to make CI green.
7. Never commit, push, merge, or publish without the user's explicit instruction.

## Stack and boundaries

Preferred frontend stack is **React + TypeScript + Vite**, with Chart.js for visualization. Tailwind CSS v4 may be used if configured and justified; the original academic concept was HTML/CSS/JavaScript, so do not add a broad UI framework just for appearance. Follow the actual initialized project rather than assuming a dependency exists.

Suggested ownership (create directories only when actually needed):

- `src/api/`: typed HTTP client, request/response parsing, error mapping, and cancellation.
- `src/features/benchmarks/`: benchmark setup, jobs, progress, comparisons, and history.
- `src/components/`: genuinely reusable presentational controls and accessible primitives.
- `src/index.css`: shared styles and Tailwind entry point if Tailwind v4 is in use.
- `tests/`: unit/component tests; `e2e/`: Playwright browser workflows.

Use a consistent `@/` alias for genuinely cross-feature imports; near-relative imports are fine. Do not import from private backend Python modules or duplicate server algorithms in the browser. Keep network logic out of chart components. Prefer feature-local state until there is a demonstrated need for global state.

## TypeScript and ESLint: enforce the strict policy

- Enable strict TypeScript checks (`strict: true`) with project-appropriate tsconfigs. Prefer typed API contracts, discriminated unions, `unknown` with validation, and exhaustive state handling.
- **No explicit `any`** or unjustified type assertions. Do not ignore rejected Promises or silently suppress errors.
- Treat `eslint.config.js` and `tsconfig*.json` as the sources of truth; use the supplied flat-config blueprint adapted to installed package versions, not a handwaved approximation.
- Preserve `@eslint/js`, `@typescript-eslint/eslint-plugin` and parser with type-aware project service; React, React Hooks, React Refresh, jsx-a11y, SonarJS, and `globals`.
- Preserve strict rules covering duplicate imports, nested ternaries, `prefer-object-has-own`, explicit `any`, unnecessary assertions, floating Promises, consistent type-only imports, unused variables, and deep parent-relative imports.
- Prefer the `@/` alias when an import would traverse **two or more** `../` segments, matching the provided lint convention.
- Keep SonarJS cognitive complexity at **warning threshold 15** and preserve requested React/accessibility warnings unless a documented compatibility issue requires a narrow change.
- When using Tailwind CSS v4, configure `eslint-plugin-better-tailwindcss` against the actual stylesheet entry point and enable canonical class enforcement. Do not enable Tailwind-specific rules against a nonexistent setup.
- Keep the ESLint project service aware of Vite/Vitest/Playwright config files and tests. Adapt exact package/rule names to the versions actually installed; report any unavoidable rule substitution.
- No blanket ESLint disables, `@ts-ignore`, `eslint-disable` at file scope, or lowered CI thresholds without review. Local narrowly scoped exceptions require a comment and explanation.

## UI/UX rules

- Prioritize data comprehension over visual flair. Clearly distinguish single-run measurements from averages; label units, worker counts, sample sizes, timestamps, and benchmark conditions.
- Provide controls for dataset, operation, worker count, and start action; accurately reflect `QUEUED`, `RUNNING`, `COMPLETED`, `FAILED`, and any supported `CANCELLED` state.
- Use semantic HTML, real buttons/labels, keyboard navigation, visible focus, adequate contrast, and text alternatives. Do not emulate system status bars or device chrome.
- Support narrow and wide screens without overflow; use responsive layout, readable charts/tables, and scrollable data regions. Respect reduced-motion preferences.
- Display loading, empty, validation error, offline, failed job, stale result, and retry states. Async fetches must handle component unmount, race conditions, and current selection changes.
- Do not make a success toast after a `202` response that implies image processing completed. Show the job as queued/running until confirmed terminal status.
- Guard against excessive polling, duplicated requests, and accidental repeated submissions. Respect backend overload responses and applicable `Retry-After`.
- Charts should cover execution time, images/sec throughput, CPU usage, memory usage, measured vs ideal speedup, and parallel efficiency. Use correct units and never silently mix HTTP requests/sec with image throughput.
- Display sample count, mean/variance information, and methodological caveats where appropriate. Distinguish theoretical ideal speedup from measured values.
- Accessible chart alternatives are mandatory: explanatory text or tabular equivalents for key metrics, meaningful labels, and readable legends. Do not rely on color alone.
- Mock responses are allowed **only** in tests and clearly identified development fixtures. Never bundle them as unexplained real production benchmark data.

## API integration and data integrity

- Define a typed client around the documented endpoints (the actual API contract is authoritative), for example datasets, job creation, job status, job metrics, and benchmark history.
- Keep request validation and server error codes distinct from transport failures. Never swallow HTTP errors or replace absent metrics with zero.
- Missing, failed, or incomplete run metrics must render as unavailable, not synthetic success.
- Stable job identifiers and dataset identities must be preserved across navigation and refresh where the API supports recovery.
- Run polling must terminate on terminal state, timeout, or explicit navigation/cancellation as designed; avoid overlapping intervals and outdated state writes.
- Backend CSV/JSON exports are authoritative. If frontend exports are introduced, preserve metadata, raw run identity, units, and provenance.
- Do not embed secrets or private tokens in browser code, URLs, logs, or screenshots.

## Testing and CI requirements

Use **Vitest + React Testing Library** for unit/component behavior and **Playwright** for genuine browser E2E. Mock network edges in unit tests; use a deterministic backend or controlled fixtures for E2E, with the distinction documented. Cover at least:

- worker/dataset controls and validation;
- job acceptance vs completion and accurate lifecycle indicators;
- progress polling, stale-response races, teardown, and retries;
- successful, failed, empty, and partial/no-metrics states;
- chart labels/units, worker comparisons, theoretical vs measured series;
- responsive layout, keyboard navigation, and critical accessibility assertions;
- a core flow: choose workload -> start job -> observe completion -> review metrics/history.

Do not assert exact wall-clock benchmark timings in ordinary unit or E2E CI. Avoid brittle screenshot-only tests; screenshots may supplement functional and accessibility assertions. For real performance testing use a separate reproducible experiment suite.

Once package scripts exist, run from `apps/web/`:

```powershell
npm ci
npm run lint
npm run typecheck
npm run test:unit
npm run build
npm run test:e2e
```

Verify the scripts before claiming they exist. CI should run lint, typecheck, unit tests, production build, and Playwright E2E when the corresponding project milestones are implemented. Run Lychee link checking as a repository-wide documentation CI gate, pin its action/tool version, and document exclusions for intentionally non-public or temporary links. Do not mark mandatory workflows as `continue-on-error`.

## Documentation and change completion

Keep the frontend README/setup instructions and API-contract usage updated. Record UX decisions that materially affect reproducibility or interpretation, especially units, missing values, aggregation, and limitations. At the end of each phase, report modified files, architectural decisions, complete checks performed with actual results, user-visible changes, open risks, and follow-up items. Do not continue into the next phase without authorization when the user wants phased execution.
