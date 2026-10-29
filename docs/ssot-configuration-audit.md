# Milestone 01 configuration ownership audit

Audit date: 2026-10-29. Scope: behavior-preserving configuration ownership, with
accurate browser hints. Later roadmap milestones are excluded.

## Repository baseline

- Workspace initially had clean `main` at
  `2855c517b7f57c4df8ee4c8c8a7f09b57e518336`.
- Fetched `origin`, then fast-forwarded clean `main` to
  `121e5be5931c752cc8b4b5d5128f6fcd903f8716`, the merged PR #13 commit.
  This is the implementation base. Existing branches were preserved.
- Local environment: Windows, CPython 3.13.9, uv 0.11.32, Node 24.18.0,
  npm 11.6.4. Dependencies and lockfiles are unchanged.
- Baseline: backend **500 passed, 2 skipped**; frontend **179 passed**;
  Ruff lint/format, Pyright, Prettier, ESLint, TypeScript, import normalization,
  repository text checks, Vite build and bundle inspection passed.
- Ten new characterization cases passed against unchanged production code before
  implementation. A controlled snapshot was captured before production edits.
  Additional differential evidence used sources archived from the exact base.
- No commit, push, PR, merge publication, deployment, or later milestone was performed.
  The local fast-forward is the sole history update.

## Inventory and canonical owners

Paths below are relative to the repository root. Tests deliberately retain concrete
expected values as independent regression evidence; they are not runtime policy.

| Concept and classification                                                                                                       | Current authority, duplicates and consumers                                                                                                                   | Decision and consequence                                                                                                                                                                      |
| -------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Worker matrix `1/2/4/8`: scientific invariant                                                                                    | `apps/server/tachyron/benchmarking/models.py:WORKERS`; coordinator previously multiplied by literal `4`; browser `api/contracts.ts:workers` and form captions | **KEEP** scientific owner; derive coordinator count and form captions. Keep typed cross-language wire tuple. Changing the matrix still requires methodology review.                           |
| Processing CLI/worker accepted counts: resource and execution contract                                                           | `cli.py` argparse choices and `workers/__init__.py` validate process-pool use, including standalone nonbenchmark processing                                   | **KEEP** lower-layer checks. Importing benchmark policy into workers/processing would reverse dependency direction. Real spawn tests verify the supported values.                             |
| Repetitions `5`, seed `2026`, interval `0.1`, tiny `false`: experiment defaults                                                  | `BenchmarkConfig`; repeated in `benchmark_cli.py`, `jobs/models.py:RunRequest` and browser form                                                               | **KEEP** benchmark ownership; adapters read immutable `DEFAULT_CONFIG`. Browser has one typed local default object. Serialized request defaults stay identical.                               |
| CLI tiny default `1` versus API/browser tiny default `5`: experiment input                                                       | Benchmark CLI explicitly supplies one repetition when `--tiny` omits `--repetitions`; API/browser retain five unless selected otherwise                       | **KEEP** intentional difference, now characterized. Do not force equivalent defaults across different entry points.                                                                           |
| Repetitions `5–20` or tiny `1–20`, seed unsigned 32-bit, interval `0.01–10`: scientific/schema constraints                       | `BenchmarkConfig` validation, Pydantic request fields and browser hints                                                                                       | **KEEP** validation at each boundary. Defaults are not minimum constraints. Changing acceptance/rejection is outside this refactor.                                                           |
| Output `256×256`: transform defaults                                                                                             | `processing/models.py:TransformConfig`; processing CLI, benchmark CLI and API repeated literals                                                               | **KEEP** processing ownership; adapters read immutable `DEFAULT_TRANSFORM`. Browser state and request share its local defaults.                                                               |
| Output dimensions `1–4096`: input contract and safety boundary                                                                   | `TransformConfig`, Pydantic fields and form validation/attributes/error text                                                                                  | **KEEP** backend validation. Consolidate repeated browser hints in the form. This differs from input-image maximum dimension `16,384`.                                                        |
| Files `1,000`, per-file `32 MiB`, compressed total `512 MiB`, pixel/entry/depth caps: resource safety                            | `processing/models.py:ResourceLimits` and `DEFAULT_LIMITS`; import defaults already derive three values; metadata file-count schema repeated `1000`           | **KEEP** processing authority; metadata schema now derives its cap. Admission, decoding, paths and output pixel checks are unchanged.                                                         |
| Registry/registration count `32`: separate operator/resource limits                                                              | `Settings` registry bound versus `ImportLimits.registrations`; depth also happens to be `32`                                                                  | **KEEP** separate meanings. Equal numbers do not establish common ownership.                                                                                                                  |
| Metadata `512 KiB`: protocol/resource bound                                                                                      | `imports/models.py:METADATA_BYTES`; endpoint repeated expression; browser repeated `524288`                                                                   | **KEEP** import owner; endpoint references it, browser decodes existing `metadata_bytes` and applies UTF-8 bytes. No endpoint field is added or changed.                                      |
| Multipart body `521 MiB`, frames `1 MiB`, chunks `64 KiB`, headers `8192`: transport budgets                                     | `ImportLimits` and import protocol constants; framing/envelope tests exercise boundaries                                                                      | **KEEP** import domain. Body allowance includes transport overhead; it is not interchangeable with input bytes.                                                                               |
| Receipt `8192` bytes and catalog/storage quotas: persistence/resource policy                                                     | `imports/storage.py:RECEIPT_BYTES`, `ImportLimits`                                                                                                            | **KEEP** distinct from equal-sized header budgets. Receipt bytes/schema/storage are unchanged.                                                                                                |
| Receive `600 s`, image `60 s`, inactivity `30 s`: import budgets                                                                 | Backend `ImportLimits`; browser upload deadline derives receive/image budgets, file count, and a local `30 s` response allowance                              | **KEEP** server authority and derived deadline. Response allowance and server inactivity are different concepts.                                                                              |
| History root, imported root, dataset allowlist, queue capacity `2` in `1–16`, Host/Origin allowlists: operator/security settings | Previously `jobs/coordinator.py:Settings`; API construction, coordinator, middleware, scripts and tests consume them                                          | **KEEP** same validated class in `tachyron/settings.py`; preserve legacy import identity. Filesystem overlap/link validation stays with startup/storage owners.                               |
| Environment versus embedded imported-directory fallback: operator setting                                                        | Environment defaults to `datasets/imported`; embedded `Settings(dataset_dir=None)` derives a sibling of the history directory                                 | **KEEP** distinct documented behavior, including empty environment strings and existing parsing errors.                                                                                       |
| Browser pre-policy file-size hint `32 MiB`, review cap `10,000`: presentation/preflight safety                                   | `useDatasetImport` reviews files while fetching policy; `start()` requires policy and checks server limits                                                    | **KEEP** bounded preflight hints. They cannot authorize upload; no new independently writable frontend safety policy is introduced.                                                           |
| JSON request `10 s`, health probe deadline, retry bounds and polling intervals: transport/lifecycle settings                     | `api/benchmarks.ts`, `api/datasetImports.ts`, `api/health.ts` and their request-owning hooks                                                                  | **KEEP** independent owners. Similar durations do not justify changing cancellation, retry scheduling or error behavior.                                                                      |
| HTTP-status messages versus import error-code messages: user-facing copy                                                         | Benchmark client sanitizes by HTTP status; import client maps allowlisted codes and retains uncertain admission outcomes                                      | **KEEP** separate maps. Their meanings and available evidence differ; merging them would erase useful transport semantics.                                                                    |
| Liveness, benchmark readiness, import READY, cancellation and unknown outcome: protocol/state and messages                       | `useApiHealth`, diagnostics, import/job hooks and notification episodes                                                                                       | **KEEP** existing state machines, role/aria-live behavior and recovery ownership. **DEFER** a typed health-state redesign; string comparisons alone do not justify broad lifecycle work here. |
| Repeated form defaults in state, request, operation selector, hints and worker captions: presentation/derived values             | `JobForm.tsx` repeated seed/interval/operation and range copy                                                                                                 | **KEEP** ownership local; share typed defaults/ranges and derive captions. Retry hints now display the retained seed/interval without rewriting the request.                                  |
| Pipeline names, schema versions, hash lengths, units and timing/CPU/cache policies: protocol/scientific identity                 | Domain records, serializers, decoders and documentation                                                                                                       | **KEEP** explicit wire/schema checks and independent tests. No schema, digest, measurement or unit change.                                                                                    |
| Vite target/ports and test helper ports: tooling settings                                                                        | `vite.config.ts`, Playwright and startup scripts; security origins explicitly allow approved local ports                                                      | **KEEP** tooling ownership. Do not generate production security allowlists from developer tooling.                                                                                            |
| Private paths, runtime secrets and recorded source identity: security/provenance                                                 | Operator settings and backend provenance                                                                                                                      | **KEEP** server-only; no settings endpoint, VITE secret, frontend path or source-hash bypass.                                                                                                 |

## Architectural decisions and tradeoffs

**KEEP** one owner per concept, without a global registry. Extracting Settings is
justified because API construction, HTTP trust policy, storage and test tooling
need operator configuration independently of queue orchestration. Its fields,
constructor order, validation, environment parser and defaults were moved unchanged.
The coordinator still imports Settings, so existing callers receive the identical
class. New production callers use the canonical module. Settings still belongs to
the API extra because its import policy depends on existing Pydantic contracts.

**KEEP** immutable default instances beside the two existing domain dataclasses,
following the existing `DEFAULT_LIMITS` pattern. These expose already validated
defaults without introducing a second list of values, runtime parsing or factories.
No validation bounds were equated with defaults.

**REJECT** a root frontend `config.ts`, feature `copy.ts` scaffolding, generic
configuration registry and generated Python/TypeScript configuration. No shared
presentation consumer justified a new module. Also reject configurable scientific
worker counts, merging unrelated equal literals, broad message rewriting, dependency
additions, benchmark changes and quality-policy changes.

**DEFER** preference persistence, a generated wire schema, deployment settings and
typed diagnostic-state redesign. They require separate product/architecture scope.

## Implementation and before/after examples

Production changes:

- `apps/server/tachyron/settings.py`: canonical operator settings; compatibility
  import remains in `jobs/coordinator.py`. `api.py` and
  `apps/web/scripts/serve-test-api.py` use the canonical import.
- `benchmarking/models.py` and `processing/models.py`: immutable default instances.
  `jobs/models.py`, `benchmark_cli.py` and `cli.py` consume those defaults.
- `jobs/coordinator.py`: `4 * (repetitions + 1)` becomes
  `len(WORKERS) * (repetitions + 1)`; the result remains 24 for a normal default run.
- `imports/models.py`: schema file cap derives from processing policy.
  `imports/routes.py`: `512 * 1024` becomes `METADATA_BYTES`.
- `apps/web/src/api/datasetImports.ts` and
  `features/datasets/hooks/useDatasetImport.ts`: browser metadata cap comes from the
  existing policy response. Missing/malformed values fail policy loading instead
  of substituting an independently maintained cap.
- `features/benchmarks/experiments/JobForm.tsx`: local typed defaults and range hints;
  worker labels derive from the existing typed tuple. Retained request seed `7`
  and interval `0.25` now show `Seed 7 · sample interval 0.25 s`; ordinary defaults
  still show `2026` and `0.1`. Submission/retry data is unchanged.

Regression changes: new `apps/server/tests/test_configuration.py`, extended
`installed_package_probe.py`, and frontend `App.test.tsx`, `DatasetImport.test.tsx`,
`datasetImportTransport.test.ts`, `useDatasetImport.test.ts`. Policy fixtures were
updated to include the already-existing server field. `docs/api.md` documents the
canonical import and links this report. No public endpoint or default changed.

## Compatibility and security evidence

Exact baseline-versus-working-tree comparison used controlled provenance and the
same generated image bytes. All nine snapshot groups matched byte for byte:
default request JSON, OpenAPI, portable manifest JSON, manifest digest, context
digest, default seeded plan, persisted history document/result/key, padded original
READY receipt bytes, and all five JSON/CSV exports. The refactored code reopened
the original SQLite history and catalog, read original exports, and loaded a
Settings pickle produced before extraction.

Selected SHA256 evidence (identical before and after):

| Encoded snapshot                 | SHA256                                                             |
| -------------------------------- | ------------------------------------------------------------------ |
| Default request                  | `b043b4a7326f919312d99aefe2c44192155be567f8d2d4ed3c11f989d12a8a1b` |
| OpenAPI                          | `871c9363cd7ced7c9f8338defa4a533be1cf2cf5eb30331ea3d7c305426851ad` |
| Controlled experiment document   | `c048090fd551230bf53c2c900722a3d27620c8f33ec23cc512597919c9eff00f` |
| Persisted history group          | `b0a29707da75cf65e6e256a06f825ca7ee81f1fc641d75b59b123ef03c7b3d24` |
| Original padded receipt snapshot | `8a24e2d2585353fb4ad4bf91a6244f69d113f51d51a2272d058d6abe4198c915` |

These are SHA256 values of encoded snapshot groups, not production context IDs.
Source hashing remains unchanged and covers loaded package sources. New code
therefore has a new source fingerprint, and a fresh live context can differ.
Recorded inputs and evidence are never rewritten to conceal that distinction.

Security/failure review covers unchanged queue and dataset registry rejection,
Host/Origin/header checks, unsafe roots and namespace overlap, cancellation,
partial initialization, helper/pool ownership, unknown outcomes and sanitized errors.
No lifecycle code, persistence schema, image transformation, seed scheduling,
warm-up policy, telemetry unit or numerical algorithm changed. Import direction is
acyclic in exercised entry points; base package isolation and API-extra probes are
part of validation. Browser metadata boundaries include exact accepted size,
one byte over, multibyte UTF-8 and malformed/missing policy values.

## Validation results

All commands ran from the repository root unless noted. `UV_CACHE_DIR` pointed to
the ignored workspace cache. Tests needing junctions, SQLite/native filesystem
access, real processes or Chromium ran outside the restrictive command sandbox.

| Command                                                                                    | Final result                                                                      |
| ------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------- |
| `uv run --locked --project apps/server ruff check .`                                       | PASS                                                                              |
| `uv run --locked --project apps/server ruff format --check .`                              | PASS, 74 files formatted                                                          |
| `uv run --locked --project apps/server pyright --project pyrightconfig.json`               | PASS, zero errors/warnings                                                        |
| `uv run --locked --project apps/server pytest apps/server/tests -q -rs`                    | PASS, **511 passed, 2 skipped**, including real spawn                             |
| `npm --prefix apps/web run format:check`                                                   | PASS                                                                              |
| `npm --prefix apps/web run lint`                                                           | PASS, zero-warning policy unchanged                                               |
| `npm --prefix apps/web run typecheck`                                                      | PASS                                                                              |
| `npm --prefix apps/web run imports:check`                                                  | PASS, 64 scanned, zero affected/unresolved                                        |
| `npm --prefix apps/web run test:coverage`                                                  | PASS, **188 tests** in 19 files; statements 90.24%, branches 84.41%, lines 92.32% |
| `npm --prefix apps/web run build`                                                          | PASS                                                                              |
| `npm --prefix apps/web run bundle:check`                                                   | PASS, 480,492 raw / 154,176 summed per-file gzip JS bytes                         |
| `npm --prefix apps/web run test:e2e`                                                       | PASS, **12 browser tests**, 50.6 seconds, fresh production build and real API     |
| `uv run --locked --project apps/server python scripts/check_repository.py`                 | PASS                                                                              |
| `uv build --project apps/server --out-dir .cache/ssot-dist`                                | PASS, wheel and sdist                                                             |
| Isolated distribution probes, detailed below                                               | PASS for both base/API installs of both artifacts                                 |
| Baseline differential serialization and persistence probe                                  | PASS, nine identical snapshot groups and original records readable                |
| Settings class AST comparison against base                                                 | PASS, fields, validation and environment parsing unchanged                        |
| Cached Lychee 0.24.2, `--offline --include-fragments=anchor-only README.md 'docs/**/*.md'` | PASS, zero errors; external URLs excluded by offline mode                         |
| Cached actionlint 1.7.12, all five `.github/workflows/*.yml` files                         | PASS                                                                              |
| `git diff --check` and final changed-file review                                           | PASS                                                                              |

The bundle changed by +430 raw / +170 summed gzip bytes from the baseline;
the unchanged checker is informational and imposes no size threshold.

Installed-package validation used `uv export --locked --project apps/server
--extra api --no-dev --no-emit-project --no-hashes --output-file
.cache/ssot-constraints.txt`, followed by a separate `uv venv` per artifact,
`uv pip install --python <venv-python> --constraint .cache/ssot-constraints.txt
<artifact>`, and isolated `python -I apps/server/tests/installed_package_probe.py`
from outside the repository. Each environment first proved API dependencies were
absent, then exercised sequential processing, real spawn and tiny 1/2/4/8-worker
benchmarking with identical recomputed exports. Installing `<artifact>[api]` with
the same constraints and running the probe with `--api` verified canonical/legacy
Settings identity and API imports/health/OpenAPI. Cross-drive hardlinks fell back
to copies; installation and probes succeeded.

The ignored local helpers `.cache/ssot-package.ps1` and `.cache/ssot_compat.py`
retain the executed package and differential procedures. The latter compared
archived base sources with the working tree using separate output directories;
temporary databases, snapshots and builds are excluded from the Git diff.

Two backend cases skip because this Windows account cannot create the requested
symlinks: `test_import_security.py:225` and `test_processing.py:400`. Both also
skipped at baseline. Other path/namespace and native process checks ran.

Earlier attempts are not counted as successful final evidence: sandbox cache/temp
operations and junction fixtures failed until access was corrected; one concurrent
backend/browser run had 29 workload-lock failures; an early browser run used the
baseline build after an interrupted build. The final backend, package and browser
integrations ran sequentially. Type inference and missing policy test fields found
during implementation were fixed before successful final checks. A local package
helper initially selected uv's `.gitignore` along with distributions; its artifact
filter was corrected before both complete isolated probes passed. No assertion,
diagnostic, lifecycle lock, scientific policy or quality gate was weakened.

## Independent review and remaining limits

The complete tracked diff and new files are reviewed against
`121e5be5931c752cc8b4b5d5128f6fcd903f8716`. Review challenges include default/minimum
confusion, CLI tiny behavior, import-time side effects/cycles, optional API isolation,
retry data preservation, UTF-8 lengths, terminal-state meanings and provenance.

Maintainability classifications: **FIX** misleading Settings ownership and duplicated
adapter/metadata/form facts (implemented); **KEEP** independent trust-boundary
validation, wire constants and error/state owners; **DEFER** broader schema and
diagnostic-state redesign. No speculative abstraction or test suppression was added.

Hosted Linux/Windows Python 3.11–3.14 CI, CodeQL and CodeScene have not evaluated
these uncommitted changes. Local Windows evidence cannot establish hosted or
cross-platform success. Known Windows symlink-privilege skips are reported with
the final backend result. No claim is made that all possible bugs are eliminated.

The final change consists of 14 production/script files, six test files and two
documentation files, including new files. Lockfiles, dependency declarations,
CI/CodeScene policy, storage/security and worker-lifecycle modules, and experimental
evidence remain unchanged. Changes are uncommitted on local `main`; any later
publication must first use an authorized conventional branch, never `codex/`.

**Recommendation: READY for maintainer review of Milestone 01.** All known in-scope
blockers are resolved and available local checks pass. Hosted matrix/security/CodeScene
evaluation remains a separate prerequisite to any later merge decision.
