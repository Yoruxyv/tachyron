# Tachyron engineering contract

These instructions apply repository-wide. Read the applicable
[backend](apps/server/AGENTS.md) or [frontend](apps/web/AGENTS.md) instructions for
domain ownership, language rules, and testing requirements. The
[quality guide](docs/quality.md) documents the checked-in tools and commands.
Suggested future architecture and tests do not expand the user's approved scope.
Report conflicts between instructions, configuration, and implementation before
changing policy.

## Investigate and decide

- Inspect requirements, architecture, contracts, data ownership, dependencies,
  callers, and existing tests before implementing. Identify compatibility,
  security, integration, and failure risks.
- Compare meaningful alternatives and explain significant tradeoffs. KEEP,
  DEFER, and REJECT are valid outcomes; sophistication alone does not justify work.
- Prefer the smallest cohesive solution. Similar code need not share an
  abstraction when providers, modules, or components could evolve independently.
  Avoid speculative frameworks, wrappers, service layers, and extension points.

## Build for understanding and correctness

Before finalizing production code, review responsibilities and control flow:

- Look for unrelated responsibilities, poor cohesion, God Functions, excessive
  length, cyclomatic/cognitive complexity, and repeated nested conditionals or
  Bumpy Road patterns.
- Give validation groups clear invariant boundaries. Prefer readable guard
  clauses and well-named helpers that own genuine responsibilities. Evaluate the
  entire affected workflow after extraction: new helpers, call chains, coupling,
  cohesion, navigation overhead, and total cognitive burden. Lower cyclomatic
  complexity in the original function alone does not establish improvement.
- Review exception propagation, partial initialization, cancellation, timeouts,
  resource ownership, and cleanup together. Check trust boundaries, authorization,
  error sanitization, credential exposure, and namespace isolation where relevant.
- Challenge unnecessary dependencies, indirection, duplication, and coupling.
  Explain justified exceptions; do not invent additional line-count or perfect-score
  requirements.

Preserve public contracts, compatibility, validation acceptance/rejection, error
behavior, deterministic results, numerical semantics, concurrency safety, and
security unless a behavior change is explicitly approved. For behavior-preserving
refactors involving critical invariants, establish available baseline evidence and
targeted boundary/failure characterization tests before editing. If pre-change
testing is impractical, document why and establish equivalent regression evidence.
Do not add tests solely to fulfill a procedure. Never remove checks, alter errors,
or split lifecycle-sensitive code merely to improve a complexity metric.

## Verify independently

1. Review the complete changed-file list and actual diff, including new files,
   against the base branch. Check for unintended behavior, scope changes, secrets,
   generated artifacts, and temporary files.
2. Perform a separate correctness and maintainability review after implementation.
   Challenge the implementation instead of repeating its summary: reconsider
   assumptions, inspect the actual diff, identify plausible counterexamples, and
   reassess whether the chosen design remains appropriate.
   Exercise meaningful boundaries and failure paths; compare relevant behavior and
   deterministic outputs before and after nontrivial refactors.
3. Run applicable tests, lint, typing, formatting, repository checks, and affected
   platform/integration checks. Fix confirmed in-scope blockers and revalidate.
4. Classify remaining maintainability findings as FIX, KEEP, or DEFER with reasons.
   Report commands actually run, results, skipped checks, limitations, and risks.

Apply the same scrutiny to AI-generated and human-written code. For substantial
changes, explain the chosen implementation, important invariants, considered failure
scenarios, test evidence, tradeoffs, and uncertainties so a maintainer can review
independently. Green CI, typing, and CodeScene results do not establish architectural
or scientific correctness. Keep reports concise and avoid unsupported readiness claims.

## Treat quality signals as evidence

Distinguish Code Health analysis/scoring, critical and advisory findings, PR-blocking
gates, and explicitly assigned refactoring goals. Use observed CodeScene findings
to guide judgment. Give critical findings and hotspot deterioration particular
attention; consider advisories even when configured as non-blocking feedback.
Honor the checked-in gate policy without refactoring solely for a score.

Quality-policy changes, including CodeScene rules, weights, thresholds, gates,
suppressions, and CI enforcement, require separate technical justification and
explicit user approval. Never weaken validation, tests, lint, typing, or workflow
protections to obtain green checks.

## Control scope and Git operations

- Never push directly to `main`, `master`, or another protected/default branch.
- Use a conventional category and lowercase kebab-case branch name, such as
  `feat/parallel-workers` or `docs/engineering-standards`; never use `codex/`.
  Verify the branch before pushing.
- Never commit, push, open a PR, merge, publish, or begin the next implementation
  phase without explicit user authorization.
