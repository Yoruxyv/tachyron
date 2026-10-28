# Phase 06 implementation and validation

Local review completed on 28 October 2026 (Asia/Jakarta), on
`feat/react-dashboard`, against `main` at `7f8073e`. This records the implementation
checkpoint before publication approval: changes were then uncommitted, with no
push, PR, merge, publication, or Phase 07 work performed.

## Delivered behavior

The dashboard submits real fixed-matrix comparisons, follows durable batch
progress, requests cancellation, restores accepted/unconfirmed submissions across
same-tab reload, and displays persisted history and six measured charts. The
backend pipeline, numerical statistics, API contracts and exports are unchanged.
Unsuccessful comparisons and unavailable telemetry never become synthetic results.

The supplied final HTML was rendered and compared with real-run screenshots.
Its dark palette, emblem, 238-pixel sidebar, header, KPI row and asymmetric overview
grid are retained. Native controls, explicit tiny/unavailable states, a fixed
execution matrix and added statistical details are intentional differences.
The overview's measured table uses a native disclosure; Compare exposes it fully.
Mobile navigation expands in page flow, restores focus on Escape, and does not
overflow the page at 375 pixels. Exact typography uses installed font fallbacks.

See the [dashboard guide](dashboard.md) for architecture, units, setup, JSDoc
conventions, every adopted script, exact npm commands, safety limits, dependency
justification and CI behavior. Production contains no fixture data or load-test
simulator. The browser helper's generated images are test-only.

## Separate correctness and maintainability review

The review considered the actual tracked diff and new files against `main`,
including source, tooling, tests, lockfile, CI and documentation. No generated
reports, screenshots, cache directories, credentials or reference HTML were added
to the tracked change. Existing quality-policy files and ESLint rules are unchanged.

**FIX:** addressed ambiguous submission retries after HTTP 5xx, duplicate admission,
stale cancellation/progress replies, obsolete history-detail responses, pagination
returning to a completed job, missing recovered jobs, blocked session storage,
route-change scrolling, skip-link routing, and mobile Escape focus recovery.
The import tool now preserves valid aliases/nearby imports, verifies identical
resolution, avoids index shadowing and namespace escapes, escapes literals, and
finishes strict unresolved checks before any write. A blocked strict write no
longer claims that updates completed.

**KEEP:** App composes views; the HTTP client and decoder own distinct trust-boundary
responsibilities; lifecycle hooks own requests, timers and recovery; charts own
their instances and no network calls. The decoder is explicit contract validation,
not a general schema framework. The standalone AST normalizer remains cohesive.
No generic state, router, resolver adapter, UI framework, or speculative extension
layer was added. The cognitive-complexity warning found during implementation was
resolved by giving submission-rejection/recovery handling a clear invariant boundary;
the complete call chain was reviewed, and lint passes with zero warnings.

**DEFER:** unsupported operations, HTTP load testing, individual live worker
telemetry, additional browser/platform matrices, and public hosting. The current
API has no contracts for those features. These deferrals do not block the approved
real-job/history/comparison workflow.

Strict TypeScript, `checkJs`, `skipLibCheck: false`, and existing rule thresholds
remain intact. JavaScript script coverage was expanded to include `.js` files.
Specific JSDoc types and narrowing replace implicit types; no explicit `any`, type
suppression, file exclusion, frozen-evidence change or weakened assertion was used.
Vitest retains default unit-test discovery; only the separate Playwright `e2e/`
namespace is assigned to its own runner.

## Validation evidence

Commands below use their repository-root spelling, except workflow-specific tools.
Windows validation used Node 24.18.0, npm 11.6.4, uv 0.11.32 and CPython 3.13.9.

| Check                                                                        | Result                                                                               |
| ---------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| `uv sync --locked --project apps/server --extra dev`                         | Passed; Python lock unchanged                                                        |
| `npm --prefix apps/web ci`                                                   | Passed; 547 packages audited, zero reported vulnerabilities                          |
| `npm --prefix apps/web run format:check`                                     | Passed using the existing repository-wide Prettier command                           |
| `npm --prefix apps/web run lint`                                             | Passed, zero warnings; no rule changes                                               |
| `npm --prefix apps/web run typecheck`                                        | Passed with strict library and JavaScript checking                                   |
| `npm --prefix apps/web run imports:preview`                                  | 32 files; zero changes and unresolved imports                                        |
| `npm --prefix apps/web run imports:check`                                    | Passed; read only                                                                    |
| `npm --prefix apps/web run test:unit`                                        | 79 tests passed across 10 files                                                      |
| `npm --prefix apps/web run test:coverage`                                    | 79 passed; 84% statements, 73.8% branches, 88.34% lines; no coverage threshold added |
| `npm --prefix apps/web run build`                                            | Strict typecheck and production build passed                                         |
| `npm --prefix apps/web run bundle:check`                                     | Passed; informational measurements below                                             |
| `npm --prefix apps/web run test:e2e`                                         | One real-API, real-spawn browser flow passed                                         |
| `uv run --locked --project apps/server pytest apps/server/tests -q`          | 375 passed, one platform-specific skip                                               |
| `uv run --locked --project apps/server ruff check .`                         | Passed                                                                               |
| `uv run --locked --project apps/server ruff format --check .`                | Passed                                                                               |
| `uv run --locked --project apps/server pyright --project pyrightconfig.json` | Zero errors/warnings                                                                 |
| `uv run --locked --project apps/server python scripts/check_repository.py`   | Passed                                                                               |
| actionlint 1.7.12 over all five workflows                                    | Passed                                                                               |
| Lychee 0.24.2, offline local files/anchors                                   | Zero errors                                                                          |
| `git diff --check main`                                                      | Passed                                                                               |

The full backend suite initially encountered an existing crash-recovery subprocess
timeout while other work was active. The exact test passed in isolation, then the
entire suite passed serially. Its timeout and assertions were not changed. Sandbox
cache/temp permissions also blocked some tool invocations before collection; the
unchanged checks passed with normal filesystem access. These failed attempts are
not counted as successful validation.

The system npm installation could not run. Installation and npm scripts used a
verified checkout-local copy of the repository-pinned npm 11.6.4 and an ignored
local launcher. The system installation was not changed. Comparing lockfiles
against the base found **no version changes to previously locked packages**.
Only Chart.js 4.6.1, Lighthouse 13.5.0 and Playwright Test 1.64.1 are new direct
dependencies; the guide documents each purpose.

## Bundle measurements

These are the existing checker's exact JavaScript byte measurements, with each
file gzip-compressed independently. They are not measured network transfer or
initial-route loading cost. No size budget or blocking threshold was introduced.

| Asset/state           | Raw bytes | Gzip bytes |
| --------------------- | --------: | ---------: |
| Original shell, total |   221,917 |     68,855 |
| Dashboard entry chunk |   256,331 |     79,172 |
| Separate charts chunk |   173,332 |     59,974 |
| Dashboard total       |   429,663 |    139,146 |
| Growth over shell     |   207,746 |     70,291 |

The charts chunk loads when complete measurements are displayed. The current
production CSS is approximately 18.01 kB raw, as reported by Vite. The bundle
checker itself remains unchanged.

## Lighthouse and browser evidence

Lighthouse 13.5.0 audited the production build with an isolated real API, an empty
registry and fresh temporary SQLite history. No image job was submitted. The
recorded audit timestamp is `2026-10-27T17:04:57.777Z` (28 October, 00:04 Jakarta).
Reports are retained locally beneath
`apps/web/lighthouse-reports/2026-10-27T17-04-49-165Z/`.

| Profile | Runs | Performance mean | Accessibility | Best practices |     SEO |
| ------- | ---: | ---------------: | ------------: | -------------: | ------: |
| Desktop |    5 |              100 |      100 each |       100 each | 91 each |
| Mobile  |    5 |              100 |      100 each |       100 each | 91 each |

These scores describe that audited empty-workspace artifact, not populated chart
views or arbitrary machines. The final keyboard-navigation corrections were
validated by unit/browser tests; these scores are not claimed as a fresh audit of
those later handlers. The initial Windows Chrome launcher failed during startup;
the adopted runner instead uses Playwright's owned browser lifecycle and the
supported Lighthouse CLI. Strict dependency checking was preserved.

The final browser run uses eight generated PNGs, 4×3 output, one measured repetition
per configuration and real 1/2/4/8-worker spawn pools. It checks completion, six
charts, units, selection, reload, overview, mobile overflow and Escape focus.
Screenshots and timestamped capture context remain in ignored Playwright output.
The tiny timings are functional evidence, not representative performance claims.

## CI and remaining limits

The existing Frontend Quality job adds the blocking import-policy check. The
existing Frontend Tests job adds one core browser workflow after unit coverage.
Required job names and aggregate Quality gate dependencies remain unchanged.
The separate Lighthouse workflow is advisory and outside the required aggregate:
valid low scores do not fail it; unusable reports or infrastructure failures do.
Both workflows retain their relevant artifacts for 14 days.

Hosted Actions, CodeScene, Linux/browser-platform matrices and branch-protection
settings were not executed or changed from this local checkout. No claim is made
that all possible bugs have been eliminated. No known in-scope blocker remains
after the completed checks above.
