# MEMO — Patchwork

**Patchwork** is an autonomous agent that fixes a repository's failing tests and
opens a verified pull request. Domain: repository automation.

## What I built

A production-shaped agent, not a notebook. The core is a small, provider-neutral
control loop driving a **54-tool registry** across five namespaces
(`git`, `github`, `ci`, `code`, `orchestration`). The model selects tools; the
registry dispatches by dict lookup and validates inputs against schemas derived
from the tool function signatures — so adding the 55th tool is a decorated
function, never a new branch in a dispatcher.

The flagship flow (`flows/repair.py`) is genuinely long-horizon: orient → run
suite → delegate root-cause analysis to a **read-only subagent** → patch →
re-run until green → gate the diff through a **reviewer subagent** → commit →
open PR. A real run spans well past twenty tool calls.

Three things I'm willing to be judged on:

1. **Distrust of the model's self-report.** The flow re-runs the suite itself;
   `verified_green` is true only if pytest exits zero. The integration suite
   includes a test where the model lies ("All good!") and the gate catches it.
2. **Subagent isolation is enforced, not advertised.** A subagent gets a
   `scoped(scope="read")` registry; the write tools are absent from its view and
   unreachable at dispatch. Its intermediate reasoning never enters the parent
   context — only a schema-validated object returns.
3. **The context strategy is in code** (`agent/context.py`), not left to the
   provider: pin the task and a progress ledger, keep recent turns verbatim,
   mechanically stub the bulky middle when over budget.

Production scaffolding is present throughout: a typed error taxonomy split on
`retryable`, retry with exponential backoff + jitter, a token-bucket rate
limiter in front of every external call, structured logging with a span tracer,
an eval harness with `pass@1` scoring, and 22 unit + integration tests that run
without any API key (a scripted LLM drives the real loop, sandbox, and pytest).

## What I cut (and why)

- **More languages.** Test running is pytest-only. The `ci` namespace is shaped
  to generalize (a `detect_test_command` probe exists), but I cut JS/Go runners
  to keep the verify loop trustworthy in one ecosystem rather than shaky in
  three.
- **A real patch/AST editor.** Edits are string- and line-range-based. This is
  enough for the bug classes in the fixtures and keeps diffs minimal, but it
  can't do semantic refactors.
- **Dynamic tool loading.** All 54 tool schemas are sent on every request
  (~4.5k tokens). On a large-context paid model this is irrelevant; on free
  tiers it dominates the request. I measured Groq's `gpt-oss-120b` free tier at
  ~8k tokens per request (and it counts `max_tokens` toward that cap) with a
  ~8k/min TPM — so the schema overhead alone caps how many calls fit per minute.
  Small fixes (e.g. the `off_by_one` fixture) run clean and verified-green on
  free Groq; an 8-bug repo needs a higher tier. The right fix is a tool-search /
  lazy-load step that sends only the relevant schemas per turn — see below.
- **Persistent run store + dashboard.** Traces live in memory and print as a
  summary; I did not add a database or UI. The span model is there to make that
  a small addition, not a rewrite.
- **Live-provider coverage in CI.** Provider clients and the network GitHub
  path are integration-tested against fakes/scripts, so line coverage sits at
  ~53%; the untested lines are concentrated in SDK adapters that need real keys.

## What more time would address

1. **Dynamic tool loading** — expose namespaces plus a `search_tools`/`load_tools`
   step so each turn carries only the ~8–12 relevant schemas instead of all 54.
   This is the highest-leverage item: it cuts request size ~3×, makes the agent
   viable on small free tiers, and is how registries actually stay coherent past
   fifty tools without flooding context.
2. **Tree-sitter-backed edits** and a multi-file change planner, to move from
   "fix the obvious bug" to "fix the bug whose cause is three files away."
3. A **larger, harder eval set** (regressions, flaky tests, multi-failure
   repos) and a self-repair retry budget tuned against it.
4. **Optional LLM summarization** layered onto the mechanical compactor for very
   long runs (see the defended decision below).

## One design decision I would defend

**Context compaction is mechanical (deterministic stubbing of old tool outputs),
not LLM summarization** — the alternative a reasonable engineer would reach for.

LLM summarization reads better and compresses harder. I chose against it as the
*default* for three reasons that matter more for this agent: (a) **it can't
hallucinate away a fact** — a summarizer might drop the one stack-frame the
agent needs three steps later, and that failure is silent and unreproducible;
(b) **it's free and synchronous** — no extra token cost or latency on the hot
path of a 20+ call run; (c) **it's testable** — `compact_if_needed` is a pure
function over the transcript, so I can assert it pins the task, keeps recent
turns, and lowers the token estimate (`tests/unit/test_context.py`), which I
cannot meaningfully do for a model call.

The cost is lower compression and clumsier middles. My mitigation is the **pinned
progress ledger**: the agent writes durable one-line facts that survive
compaction regardless, so the plan never depends on the bulky history. If runs
grew long enough that stubbing wasn't enough, I'd add LLM summarization *on top
of* — not in place of — the mechanical pass, keeping the deterministic floor.
