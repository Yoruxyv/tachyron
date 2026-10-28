# React dashboard and frontend tooling

The React/TypeScript dashboard configures real image-processing benchmarks and
compares saved measurements. It uses the existing Vite, Tailwind v4, strict
TypeScript, ESLint, and Prettier configuration. The API described in
[jobs and history](api.md) remains authoritative.

## Run locally

Install the locked dependencies from the repository root:

```powershell
uv sync --locked --project apps/server --extra dev
npm --prefix apps/web ci
```

Register your own dataset and start the API in one terminal:

```powershell
$env:TACHYRON_DATASETS = '{"example":"datasets/example"}'
$env:TACHYRON_DATA_DIR = 'results/api'
uv run --locked --project apps/server uvicorn tachyron.api:app --host 127.0.0.1 --port 8000
```

Start the frontend in another terminal:

```powershell
npm --prefix apps/web run dev
```

Open [the dashboard](http://127.0.0.1:5173). Vite proxies `/api` and `/health` to
the local API. `TACHYRON_API_URL` may select another operator-owned proxy target
when starting Vite; it is server configuration, not a browser credential. A static
deployment needs equivalent same-origin routing. No public hosting or authentication
is introduced. Keep this local API on loopback.

## Workflow and interpretation

- **Overview:** four measured KPIs, execution-time and CPU charts, recent records,
  and a real submission form. A disclosure exposes the full measured table and
  sample spread without displacing the main card grid. Empty metrics remain unavailable.
- **Experiments:** dataset, fixed operation, dimensions, repetitions, tiny-test
  mode, submission, durable batch progress, accounting, and cancellation.
- **Compare:** select one saved experiment and inspect 1/2/4/8-worker statistics
  with six Chart.js charts and a tabular alternative.
- **History:** 20 terminal jobs per page, dataset/job-ID search, status filters,
  selection, evidence inspection, and the raw API JSON link. Filters apply to the
  current page. Jobs and official results remain in backend SQLite.
- **API Tests:** retryable process-liveness checks. HTTP load-test measurements
  remain explicitly unavailable; there is no production load generator.

Each submission runs the backend's complete **1/2/4/8-worker process-pool matrix**.
The worker selector changes inspection, never the submitted execution plan.
The header and experiment form share one registered dataset selection across views.
Overview keeps dimensions, repetitions and tiny-test mode in **Benchmark settings**;
validation opens that disclosure when a setting needs correction. Recent experiments
uses a selectable table whose time and speedup columns follow the inspected worker.
History retains the complete six-chart evidence below its record list and inspector.
The compact sidebar shares the connection check with API Tests and displays only
API online, API offline, or a neutral checking/unknown state. Activate it to recheck.
Offline status includes a Troubleshoot action that opens a compact native dialog
over the current view. It never opens automatically or changes the route. The
dialog owns only presentation/copy feedback and reuses the shared health-check
owner; retrying does not submit or recover a benchmark. It provides the repository-root
PowerShell startup command, Copy, Retry connection and Close. API Tests retains its
independent connection diagnostics and telemetry availability, without duplicate setup
instructions. Native modal background blocking, explicit Tab boundary wrapping,
Escape/backdrop dismissal and scroll locking preserve the dashboard. Focus returns
to Troubleshoot, or to API status if reconnection removes the offline trigger.
Availability does not
establish full benchmark readiness; dataset counts and job recovery labels are omitted.
Job verification belongs to Experiments. Retained jobs and unconfirmed submissions
stay recoverable while offline, with prior job state explicitly unverified. Reconnecting
the API alone never verifies a saved job or activates the navigation activity dot.

API failures use one compact, dismissible bottom-right toast per unresolved failure
episode. Dataset, history, evidence and experiment errors
retain their own retry actions. Dismissing a toast never clears errors or submission
identity. Retries reuse the existing request owners, cooldowns and idempotency key;
they never replay cancellation automatically. Recovery is announced briefly through
one polite live region. No health polling loop, notification dependency or new backend
contract is introduced.
Retry checks connectivity first and refreshes only stale or failed resources after
success. Same-page history refresh keeps cached content and identical evidence
objects, so unchanged charts, selection and scroll position survive background work.
The action retains keyboard focus while checking; recovery moves focus to Dismiss
only when the focused Retry action is replaced. Startup waits for the existing health
check to classify the failure, without a notification delay or new polling loop.
Only `resize-grayscale-v1` is offered: Lanczos resize and grayscale, with the
established image safeguards. No unsupported prototype operation is implied.
Select-only comboboxes use the HTML's dark menus, with compact header and full-width
form placements. Dataset and saved-record selections stay with their existing owners.
Operation menus contain the single supported operation. Arrow keys, Home/End,
typeahead, Enter/Space, Escape, Tab and outside clicks are supported; focus stays on
the trigger with `aria-activedescendant`. Menus flip or align within the viewport.
A thin muted focus outline remains visible for keyboard users.
The logo follows the final HTML's Inter wordmark (20px, weight 710, 1.12 line height,
−0.065em tracking) and subtitle (9px, weight 520, 1.2 line height, 0.12em tracking),
with a 3px gap. The 38px emblem keeps its original paths and square line caps.
The approved mobile navigation/layout remains unchanged. A local text layer matches
the reference mobile sidebar's transformed text rendering without shifting the logo.

HTTP 202 means accepted. Progress counts completed batches, including warm-ups.
Image counters count repeated attempts, not unique images. Cooperative cancellation
waits for the current batch to drain; a late request can still end in completion.
Failed/cancelled evidence never becomes a successful scaling comparison.

The client combines owner cancellation with a ten-second request deadline, uses
one nonoverlapping polling chain, and starts at a one-second polling interval.
Failures back off to 30 seconds, respecting bounded `Retry-After` delays of up to
60 seconds. Five consecutive failures or 30 minutes pause polling; **Resume status
polling** checks the same job without creating another. Unmount aborts requests.
Late replies cannot replace a newer selection or acknowledged job progress.

An unconfirmed submission retains its request and idempotency key. Retry that
submission instead of starting a different one. Same-tab session storage preserves
unconfirmed submissions and accepted job IDs across reload. If storage is blocked,
an explicit warning asks users to keep the tab open. Separate tabs have independent
submission controls; server admission and idempotency remain authoritative.

Charts project server summaries without recomputing scientific statistics:

| Metric         | Meaning and units                                                                   |
| -------------- | ----------------------------------------------------------------------------------- |
| Execution time | Mean measured batch wall time, seconds; includes spawn startup/teardown             |
| Throughput     | Mean completed images/second across measured runs                                   |
| CPU            | Observed process-tree percent of **one logical core**; can exceed 100%              |
| Memory         | Maximum sampled process-tree RSS in MiB; short peaks may be missed                  |
| Speedup        | Server ratio of means against the **one-worker process pool**; ideal is theoretical |
| Efficiency     | Server speedup / worker count × 100, in percent                                     |

The table includes measured run counts, sample standard deviation (`ddof=1`),
minimum/maximum duration, and CPU/memory observation coverage. Warm-ups are excluded
from summaries. Null observations never become zero. Tiny tests, including the
browser integration test, are prominently labelled as functional evidence rather
than representative performance measurements. Independent contexts are not pooled.
The raw API link preserves complete records and provenance; no frontend CSV
reconstruction competes with the authoritative backend exports.

## Source ownership and documentation

| Location                                             | Responsibility                                                               |
| ---------------------------------------------------- | ---------------------------------------------------------------------------- |
| `apps/web/src/App.tsx`                               | Own lifecycle hooks, focus transfer and persistent worker inspection         |
| `src/features/benchmarks/BenchmarkNotices.tsx`       | Dataset availability, request errors and job recovery announcements          |
| `src/features/benchmarks/BenchmarkWorkspace.tsx`     | Compose view layouts without owning requests or persistent selection         |
| `src/features/benchmarks/Comparison.tsx`             | Saved-evidence selection, loading/failure states and measured comparisons    |
| `src/api/contracts.ts`, `decode.ts`, `benchmarks.ts` | Consumed API types, runtime boundary validation, sanitized HTTP transport    |
| `src/components/`                                    | Shell, hash navigation, icons, chart failure boundary                        |
| `src/features/benchmarks/useJob.ts`                  | Submission identity/recovery, status lifecycle, cancellation and cleanup     |
| `src/features/benchmarks/useHistory.ts`              | Registry and history requests, paging, selected evidence and race protection |
| `src/features/benchmarks/chartData.ts`, `Charts.tsx` | Presentation-only projections and owned Chart.js instances                   |
| Remaining benchmark components                       | Setup, progress, results, history and liveness presentation                  |
| `apps/web/scripts/`                                  | Standalone frontend maintenance, audit and test-support commands             |

Paths beginning `src/` in this table are relative to `apps/web/`. Chart.js is loaded
as a separate production chunk when complete measurements are displayed. Network
requests remain outside chart components, and every chart instance is destroyed on
replacement/unmount. Chart failures leave the measured table available.

The visual reference's final cascade uses a 14px root, Inter (400–700), and IBM
Plex Mono (400–600); its earlier 13px declaration is overridden. Both font families
load from Google Fonts with local/system fallbacks. Browser-only fixtures verify
card geometry, table composition, shared controls, CPU values above 100%, and
overflow at 320–1440px without introducing production sample data. Local reference
screenshots, overlays and measurements belong in the ignored
`apps/web/visual-parity-evidence/` directory. The reference HTML is not bundled.

Use concise Google-style JSDoc descriptions and `@param`, `@returns`, `@throws`,
and verified `@example` annotations where useful. JavaScript tooling uses specific
JSDoc types, typedefs, and narrowing to support `checkJs`. TypeScript uses native
types; comments explain contracts, units, side effects, and lifecycle obligations.
Separate functions with blank lines and use readable guard clauses. Prettier owns
formatting; comments and short files do not substitute for responsibility review.

## Adopted scripts

Run these commands from the repository root. Every script resides in
`apps/web/scripts/`.

| Script                            | npm command                                                                       | Behavior                                                                    |
| --------------------------------- | --------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| `normalize-imports.mjs`           | `npm --prefix apps/web run imports:preview`                                       | Read-only preview of proposed changes                                       |
| Same script                       | `npm --prefix apps/web run imports:fix`                                           | Explicit strict write mode                                                  |
| Same script                       | `npm --prefix apps/web run imports:check`                                         | Read-only strict CI policy check                                            |
| `check-bundle-budget.mjs`         | `npm --prefix apps/web run bundle:check`                                          | Existing raw/gzip JavaScript measurements; build first                      |
| `run-lighthouse.js`               | `npm --prefix apps/web run lighthouse`                                            | Typecheck, production build, isolated real API, browser audits and cleanup  |
| `check-lighthouse-budget.js`      | `npm --prefix apps/web run lighthouse:report -- lighthouse-reports/RUN-DIRECTORY` | Report a completed audit directory; see path note below                     |
| `check-lighthouse-budget.test.js` | `npm --prefix apps/web run test:unit`                                             | Advisory thresholds and malformed/missing report regression tests           |
| `serve-test-api.py`               | Started by Lighthouse and Playwright                                              | Real API with temporary SQLite; empty by default, optional test-only images |

**Report path note:** npm executes scripts from `apps/web/`. Replace the report
argument with the **absolute directory printed by the runner**, or use
`lighthouse-reports/RUN-DIRECTORY` relative to `apps/web/`. Do not use a
repository-relative `apps/web/` prefix in the argument. For example:

```powershell
npm --prefix apps/web run lighthouse:report -- lighthouse-reports/2026-10-27T16-44-35-189Z
```

### Import normalization safety

The normalizer uses TypeScript's resolver and the existing `@/` mapping, with an
exact-file fallback for literal Vite assets. It scans `src/` and `tests/`, skips
symlink directories and declarations, and processes files in deterministic order.
It prefers aliases across feature boundaries or for paths traversing at least two
parents, preserves valid existing aliases and nearby relative imports, and checks
that each proposed spelling resolves to the same target before rewriting.
Index-shadowing candidates that would select another module are left untouched.
Symlink targets outside the source namespace are not rewritten.

The AST walk handles import declarations, re-exports, import-equals declarations,
single-argument static dynamic imports and import type references. Comments, ordinary strings,
computed dynamic imports, and arbitrary `require` calls are not rewritten. Query
and hash suffixes and literal delimiters are retained safely. The tool neither
reorders dependencies nor changes the import graph, and repeated fixes are
idempotent.

Preview exits zero. Check exits one when safe changes are needed; bad flags,
configuration diagnostics, or strict unresolved imports exit two. Strict mode
finishes unresolved-import validation before writing any file. Filesystem failures
can still interrupt a multi-file write; review `git diff` after applying fixes.
This is not a second resolver or a substitute for TypeScript, Vite, and ESLint.
Ambiguous cases are left for manual review rather than rewritten unsafely.

### Lighthouse setup and audited state

```powershell
uv sync --locked --project apps/server --extra api
npm --prefix apps/web ci
```

Then, from `apps/web/`:

```powershell
npx playwright install chromium
npm run lighthouse
```

Linux CI uses `npx playwright install --with-deps chromium`. `CHROME_PATH` may
select an installed compatible browser executable. The runner uses Playwright to
own its browser and the Lighthouse CLI as the audit boundary. Lighthouse's current
programmatic trace-engine declarations conflict with strict optional-property
checking; no dependency patches, exclusions, `skipLibCheck`, or suppressions are
used to work around them.

Ports **8001** and **4173** must be free. The runner starts the real API with an
empty registry and fresh temporary history, serves the actual production build,
and performs five desktop and five mobile audits. It submits no jobs and seeds no
production metrics. API startup requests, CLI processes and browser startup have
timeouts. Closing API stdin requests cooperative lifespan cleanup; a bounded
process-tree fallback handles failed shutdown. Browser and preview cleanup run even
after initialization or audit failures. Reports use unique directories, so earlier
evidence is not recursively deleted. `audit-state.json` records timestamp,
application state, Node version, platform and URL.

Reference targets remain Performance mean 95, individual Performance 90, and
Accessibility/Best Practices/SEO 90. **All are advisory.** Valid reports return
zero regardless of score. Missing, malformed, incomplete, runtime-error, nonfinite,
or out-of-range scores fail reporting because usable evidence is absent. A custom
budget passed to the pure evaluator changes both evaluation and displayed targets;
the CLI exposes no enforcement switch.

The separate [Lighthouse workflow](../.github/workflows/lighthouse.yml) runs for web
pull requests or manual dispatch, appends Actions summaries, and retains reports
for 14 days. It is not a prerequisite of the required aggregate Quality gate.
Infrastructure errors remain visible failures in that optional workflow; score
shortfalls do not fail it. No existing gate is weakened.

## Validation and real-run screenshots

From the repository root:

```powershell
npm --prefix apps/web run format:check
npm --prefix apps/web run lint
npm --prefix apps/web run typecheck
npm --prefix apps/web run imports:check
npm --prefix apps/web run test:coverage
npm --prefix apps/web run build
npm --prefix apps/web run bundle:check
npm --prefix apps/web run test:e2e
```

Install Chromium first as above. The core Playwright test uses production
preview on **4174** and an isolated real API on **8001**, with eight generated
test-only PNGs, 4×3 output, `tiny: true`, one repetition, and real spawn workers.
It verifies completion, six charts, units, selection, reload recovery and a
375-pixel mobile history view. Populated Overview, Compare, History, Experiments
and API views are checked at 320, 375, 390, 768 and 1280 pixels. It does not assert
exact benchmark duration.
The helper is never imported by production code.

Canvas CSS constrains stale inline widths while Chart.js responds asynchronously
to container resizing. The browser regression holds real ResizeObserver delivery
during the first mobile resize and immediately checks document and canvas
containment. It then releases notifications and waits for the bitmap dimensions
to match the container; no sleep or retry replaces the overflow assertion.
The `held-chart-resize` and `responsive-layout` JSON attachments record viewport,
document/body widths, element bounds and styles, fonts, and canvas/container sizes.
Wide metric tables retain their own horizontal scrolling without widening the page.

The test saves `real-tiny-comparison.png`, `real-tiny-overview.png`, and
`real-tiny-mobile-history.png` beneath
`apps/web/test-results/`. An attached JSON capture context records UTC capture time,
dataset origin, configuration and the functional-test limitation. Open the HTML
report with `npx playwright show-report` from `apps/web/` to inspect that context
and any failure traces. For a GIF/video, add Playwright recording locally or record
the browser while running the same real workload; preserve its timestamp and tiny
label. Do not substitute prototype simulations in screenshots or performance claims.

The existing **Frontend Tests** CI job runs unit coverage and browser tests,
retaining reports/screenshots for 14 days. Additional contract-fixture tests cover
reference geometry, dropdown layout/keyboard interaction, outage recovery and retry
stability; these fixtures are separate from the real benchmark flow. **Frontend Quality** adds the
verified import check. Existing required job names and aggregate dependencies stay
the same. Full browser/platform matrices and k6 infrastructure remain deferred.

## Dependencies and design decisions

Only three direct dependencies are added, pinned in the npm lock:

| Dependency                | Kind        | Required functionality                                             |
| ------------------------- | ----------- | ------------------------------------------------------------------ |
| `chart.js@4.6.1`          | Runtime     | Six measured comparison charts; selectively registered controllers |
| `lighthouse@13.5.0`       | Development | Production-build advisory audit CLI                                |
| `@playwright/test@1.64.1` | Development | Core real-API browser flow and owned audit browser                 |

Package identity, Node/npm constraints, package manager, existing React/Vite/
TypeScript/testing versions, and all existing validation commands are preserved.
There is no router, global state library, UI framework, or Markdown/math dependency.

- **IMPLEMENT:** final dark shell, restrained monochrome palette, branded emblem,
  sidebar navigation, compact header, KPI cards, responsive chart/record grids,
  accessible custom selects, real lifecycle/history, typed boundaries, documented
  import tooling, advisory Lighthouse, and one real core browser test.
- **KEEP:** the existing bundle checker and its informational semantics; backend
  pipeline, fixed execution matrix, numerical statistics, and exports; strict
  TypeScript/`checkJs`, ESLint, formatting and existing quality protection.
- **DEFER:** HTTP load-test execution/results, per-worker live utilization, and
  unsupported operations because the backend has no matching contracts.
- **REJECT:** simulated production metrics, fake worker progress, inherited
  Semantix branding, blocking reference Lighthouse/bundle thresholds, and wholesale
  adoption of another project's package manifest.

Intentional visual differences from the reference are truthful empty/failure
states, fixed-matrix execution, the SD/coverage table, minimal API status and toast
notifications. Mobile navigation expands in normal page flow, avoiding a modal focus
trap. Inter and IBM Plex Mono use the reference's Google Fonts request with local
fallbacks when unavailable. The header omits unsupported global operation choices,
ornamental OS chrome, and demo
identity. These differences preserve functionality, accessibility and API truth.

Review cohesion across the whole workflow: App composes; transport and validation
own distinct trust-boundary responsibilities; lifecycle hooks own their resources;
charts own no network calls. Keep the standalone normalizer cohesive rather than
creating generic resolver adapters. CodeScene scores and hosted CI results still
require their respective providers; local success does not establish those checks.
