# Contributor quality guide

## Tool ownership

The repository uses `apps/server/` and `apps/web/`; there is one Python project and
one npm dependency root. Root `ruff.toml` and `pyrightconfig.json` govern Python
source, tests, and repository scripts. Do not create nested overrides.

- Ruff owns Python linting/formatting and supported TOML formatting.
- Pyright preserves the configured error-level diagnostics and targets Python 3.11.
- Prettier owns TypeScript, TSX, JavaScript, CSS, HTML, JSON, Markdown, and YAML.
- ESLint owns frontend correctness, accessibility, hooks, typed promises, and import
  restrictions. Its rules remain intact; formatting does not run through ESLint.
- `.editorconfig` supplies UTF-8, LF, final-newline, and indentation guidance.
  `.gitattributes` retains Git's LF checkout policy and binary image declarations.

Prettier is pinned in `apps/web/package-lock.json`, with no root npm manifest.
Its commands explicitly load both root ignore files, covering the entire repository
while leaving dependencies, generated output, Python, TOML, and lockfiles alone.

ESLint 9.39.5 remains pinned because the required React and jsx-a11y plugins exclude
ESLint 10 in their peer ranges. It is [upstream EOL](https://eslint.org/version-support/);
revisit this pin when the plugins support the maintained major, preserving the rules.
TypeScript 6.0.3 remains within typescript-eslint's supported `<6.1` range.

Pillow 13's `resize` annotation references an undefined optional NumPy alias when
NumPy is absent. Two narrowly scoped `reportUnknownMemberType` comments cover the
typed tuple calls in processing and its pixel-reference test. They preserve all
argument/return checking and the root policy; NumPy is not installed just for a
third-party annotation. Revisit these comments when Pillow corrects its alias.

## Local commands

Use uv 0.11.32, Node 24.18.0, and npm 11.6.4. From the repository root:

```powershell
uv sync --locked --project apps/server --extra dev
npm --prefix apps/web ci
uv run --locked --project apps/server ruff check .
uv run --locked --project apps/server ruff format --check .
uv run --locked --project apps/server pyright --project pyrightconfig.json
uv run --locked --project apps/server pytest apps/server/tests -q
uv run --locked --project apps/server python scripts/check_repository.py
npm --prefix apps/web run format:check
npm --prefix apps/web run lint
npm --prefix apps/web run typecheck
npm --prefix apps/web run test:unit
npm --prefix apps/web run test:coverage
npm --prefix apps/web run build
npm --prefix apps/web run bundle:check
```

To apply supported formatting locally, use `npm --prefix apps/web run format`.
Use `npm --prefix apps/web run lint:fix` for ESLint's available safe fixes, and
review the resulting diff. Python formatting uses
`uv run --locked --project apps/server ruff format .`.

`test:unit` keeps its existing name; `test:coverage` runs the same meaningful tests
with the matching V8 coverage provider. Coverage includes all application source,
including the browser bootstrap, and produces text, HTML, and LCOV under the ignored
`apps/web/coverage/` directory. There is no arbitrary percentage gate.

## Repository text

`scripts/check_repository.py` is a read-only standard-library check. Git selects
tracked and untracked nonempty text, respecting ignore files and binary attributes.
No extension allowlist hides scripts or extensionless text. Symlinks are not followed.
Empty files need no encoding or whitespace validation. Declare genuine binary
fixtures as binary in `.gitattributes`; do not exclude whole fixture directories.

The checker rejects invalid UTF-8, BOMs (including embedded ones), replacement
characters, C1 controls, and reversible UTF-8 decoded through Windows-1252 or Latin-1.
The latter requires an actual byte round trip; isolated accented letters and
international scripts are allowed. Such sequences remain a heuristic: intentionally
discussing corrupted text should use escaped Unicode literals. No automatic repair
occurs, and diagnostics show locations without quoting file contents.

CR/CRLF, missing final newlines, and accidental trailing spaces/tabs also fail.
Exactly two spaces following Markdown content are allowed as intentional hard breaks.
Use backslash hard breaks when avoiding trailing spaces. Editors preserve Markdown
spaces; Prettier normalizes supported Markdown. Intentional malformed fixtures in
tests are generated at runtime, so checked-in source remains valid.

The design review considered the
[PanganKita checker](https://github.com/PanganKita-Labs/android/blob/main/.github/scripts/check_mojibake.py).
No repository license could be verified, so this checker was written
independently; source was not copied. Narrow S603 comments document resolved Git
executables and fixed arguments without a shell; the same restriction applies in
isolated test repositories. Git discovery errors, timeouts, and read failures fail
the check rather than becoming success.

## Documentation links

Install [Lychee 0.24.2](https://github.com/lycheeverse/lychee/releases/tag/lychee-v0.24.2)
and place its executable on PATH. From the repository root, these commands work in
PowerShell and POSIX shells; Lychee handles the quoted glob itself:

```powershell
lychee --offline --include-fragments=anchor-only README.md 'docs/**/*.md'
lychee --cache --max-cache-age 1d README.md 'docs/**/*.md'
```

The first command checks local files and anchors without HTTP requests. CI uses
this mode for pull requests. It deliberately has no cache, so a newly broken local
link cannot be hidden by an earlier result.

The second checks external HTTP links too, with a one-day response cache, two
retries, twenty-second request timeout, and bounded concurrency. The separate
[external link workflow](../.github/workflows/links.yml) runs weekly and manually;
remote outages fail that job and should be triaged. The only URL exclusion covers
the documented loopback development servers on ports 5173 and 8000, which are not
running in documentation jobs. GitHub and documentation domains are not excluded.

## Workflows

The main workflow is [quality.yml](../.github/workflows/quality.yml). Its display
name `CI` and original job identifiers remain stable after the filename rename;
required check names therefore remain unchanged. Local documentation references
use the new filename. Hosted branch rules still need verification when publishing
is approved; this tooling does not change GitHub settings.

Install [actionlint 1.7.12](https://github.com/rhysd/actionlint/releases/tag/v1.7.12)
and run:

```powershell
actionlint .github/workflows/quality.yml .github/workflows/links.yml .github/workflows/security.yml .github/workflows/pr-labeler.yml
```

CI verifies the downloaded actionlint archive's pinned SHA256 before execution.
No standalone shell scripts are introduced. actionlint may use ShellCheck if it is
available on the runner; there is no separate speculative ShellCheck job.

The required `Quality gate` waits for every quality job, including security checks.
It fails if any prerequisite fails, is cancelled, or is skipped. Regression tests
exercise the actual inline gate script and ensure its dependencies include every
other quality job. No branch-protection bypass is used.

The reusable `security.yml` scans Python and JavaScript/TypeScript with CodeQL and
checks committed changes with pinned TruffleHog. Secret verification is disabled
to avoid contacting credential providers; suspected secrets still fail the scan.
Container scanning and build provenance are deferred until Docker images exist.

`pr-labeler.yml` uses `pull_request_target` only for label metadata. It never checks
out or runs pull-request code, and title/body data enter the shell through quoted
environment variables. This workflow starts operating when it exists on the base
branch; it is not a required quality check for introducing the workflow itself.
Issue forms and the PR template describe Tachyron's actual scope and request
sanitized evidence and disclosure of material AI assistance.

## Bundle baseline

Build first, then run `bundle:check`. The standard-library Node script reports raw
and gzip bytes for each `.js` file directly inside Vite's current `dist/assets/`
output, plus totals in stable filename order. Gzip figures are per-file compression,
not measured network transfer or initial-route loading cost. Missing/empty build
output is an error. Size growth is informational: there are no warning or failure
budgets until representative application measurements justify them.

The reference bundle script's fixed thresholds, import normalizer's app/shared/feature
ownership assumptions, and Lighthouse scripts' product names and score budgets were
not adopted. Existing ESLint alias and no-explicit-any rules cover today's imports.
Playwright, browser performance budgets, and engine/package extraction require
actual workflows and remain deferred.
