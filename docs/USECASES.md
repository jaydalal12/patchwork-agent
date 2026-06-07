# Use cases

Patchwork fixes failing tests and opens a verified PR. What that unlocks in
practice — and, honestly, where it stops.

## 1. CI auto-repair bot

**Trigger:** a build goes red on a branch.
**Flow:** a CI step runs `patchwork run --clone <repo> --open-pr --github-repo owner/repo`
against the failing ref. Patchwork diagnoses, fixes, re-runs the suite until
green, gates the diff through its reviewer subagent, and opens a PR with a
root-cause writeup and before/after test counts.
**Guard rails:** it never pushes to the default branch and never auto-merges —
the PR plus a human review is the boundary.

## 2. Dependency-bump fallout

**Trigger:** a bot (Dependabot/Renovate) bumps a library and tests break.
**Flow:** point Patchwork at the bumped branch; it attempts the mechanical
adaptation (renamed import, changed signature, new default) and verifies green.
**Why it fits:** these breaks are usually small, local, and test-covered —
exactly Patchwork's sweet spot.

## 3. Triage assistant (even when it can't fully fix)

**Trigger:** a flaky or failing suite a human hasn't looked at yet.
**Flow:** Patchwork's `orchestration.analyze_failure` subagent returns a
structured diagnosis (root cause, suspect files, fix strategy, confidence). Even
on a partial run it reports exactly which tests stay red and its best hypothesis.
**Value:** saves the first hour of "where do I even start."

## 4. Pre-commit / local "make it pass" helper

**Trigger:** a developer wants the obvious red tests cleared before a review.
**Flow:** `patchwork run --repo .` on a local checkout (sandboxed copy — your
working tree is untouched) produces a fix branch to inspect.

## 5. Teaching / eval harness for agent behavior

**Trigger:** you want to measure how an agent does on seeded bugs.
**Flow:** `python -m patchwork.eval.harness` scores `pass@1` over fixture repos
with known bugs. Add your own fixtures to grow the suite.

---

## Honest limits (read before pitching it)

- **Python + pytest only.** The `ci` namespace is shaped to generalize, but JS/Go
  runners are not built. The verify loop is trustworthy in one ecosystem by
  design.
- **String / line-range edits, not semantic AST.** It fixes the kind of bug whose
  cause is near the failure; it won't do a cross-file refactor.
- **Scoped, well-covered failures.** Large unfamiliar codebases or failures with
  no clear test signal will exhaust the call budget.
- **Free LLM tiers are tight.** All 54 tool schemas are sent per call (~4.5k
  tokens), so big multi-fix runs need a higher tier (or the planned dynamic tool
  loading). Small fixes run clean on free tiers.
- **Not autonomous in production.** It opens PRs; it does not merge. A human (and
  the reviewer subagent) is always in the loop for the outward-facing action.
