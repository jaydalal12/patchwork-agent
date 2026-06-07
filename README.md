# Patchwork

[![CI](https://github.com/jaydalal12/patchwork-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/jaydalal12/patchwork-agent/actions/workflows/ci.yml)

An autonomous agent that **fixes failing tests in a Git repository and opens a
verified pull request.** Point it at a repo; it orients, diagnoses the failures
(delegating root-cause analysis to an isolated subagent), makes the smallest
correct change, **re-runs the suite until it is actually green**, has a reviewer
subagent gate the diff, then commits and opens a PR describing the fix.

The defining design choice is distrust: the agent never gets to *declare*
success. A separate verification step re-runs the test suite and the run is
marked `verified_green` only if pytest exits zero. A confident wrong answer
scores the same as a crash.

```
ORIENT ──▶ DIAGNOSE ──▶ FIX ──▶ VERIFY ──▶ REVIEW ──▶ SHIP
 (ci)     (subagent)   (code)   (ci, loop  (subagent) (git, github)
                                 until green)
```

## How it maps to the five required properties

| # | Property | Where it lives |
|---|----------|----------------|
| 1 | **50+ tools / 4+ namespaces, model-driven** | `tools/` — **54 tools** across `git`, `github`, `ci`, `code`, `orchestration`. The model selects; `registry.py` dispatches by dict lookup (no conditional chain). `patchwork tools` lists them. |
| 2 | **Subagent in isolated context** | `agent/subagent.py` + `tools/orchestration_tools.py`. `analyze_failure` / `review_patch` spawn a *separate* `ConversationContext` with a **scoped, read-only registry** and return a schema-validated object. A read-scoped subagent physically cannot call a write tool. |
| 3 | **Long-horizon ≥20 calls, context strategy in code** | `flows/repair.py` is a 20+ call task. `agent/context.py` holds the explicit, mechanical compaction strategy (pin the task + progress ledger, keep recent turns verbatim, stub the bulky middle). |
| 4 | **Production scaffolding** | typed errors (`errors.py`), structured logs + span tracing (`observability.py`), retry w/ exponential backoff (`resilience/retry.py`), token-bucket rate limiting (`resilience/ratelimit.py`), eval harness (`eval/`), unit + integration tests (`tests/`), `Dockerfile`. |
| 5 | **Composable tool I/O** | `ci.run_tests` emits structured failures → consumed by `ci.parse_failures` and fed into `orchestration.analyze_failure`. Tools chain on structured data, not strings. |

## Quickstart

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"          # core + tests, no LLM SDK needed to run tests
pip install -e ".[all]"          # add Anthropic + Gemini SDKs for live runs

patchwork doctor                 # check keys, git, registry
patchwork tools                  # list all 54 tools by namespace
```

Configure a provider (copy `.env.example` → `.env`). Three are supported —
**Anthropic, Groq, and Gemini** — selected by `PATCHWORK_LLM_PROVIDER` or
auto-detected from whichever key is set (preference: Anthropic → Groq → Gemini).
The agent loop is identical across all three; `llm/` abstracts the provider, and
each has a client-side RPM knob (`PATCHWORK_<PROVIDER>_RPM`) to match your tier.

Fix a local repo:

```bash
patchwork run --repo ./fixtures/off_by_one
```

Fix and open a PR (needs `GITHUB_TOKEN`):

```bash
patchwork run --clone https://github.com/you/repo.git \
              --open-pr --github-repo you/repo
```

## Tests & evaluation

```bash
pytest -q                        # 37 tests; integration drives the real loop
                                 # + sandbox + pytest via a scripted LLM (no key)
python -m patchwork.eval.harness # pass@1 over seeded buggy repos (needs a key)
```

The integration suite is the proof that the moving parts fit: a scripted model
drives the **real** registry, sandbox, git, and pytest to fix
`fixtures/off_by_one`, and a second test confirms the verify gate rejects a
model that *claims* success without changing anything.

## Architecture

```
src/patchwork/
  config.py            typed settings from env
  errors.py            error taxonomy (retryable vs not)
  observability.py     structured logging + span tracer
  resilience/          retry (exp backoff + jitter), token-bucket rate limiter
  llm/                 provider-neutral protocol; anthropic + groq + gemini impls; factory
  registry.py          model-driven dispatch, scoped views, validated execution
  tools/               git / github / ci / code / orchestration namespaces + sandbox
  agent/               conversation context (compaction), control loop, subagent harness
  flows/repair.py      the flagship long-horizon task + independent verify gate
  eval/                harness + scoring over fixture repos
  cli.py               tools | doctor | run
```

Safety: every file/test operation runs inside a `RepoSandbox` — an isolated
clone with path confinement and command timeouts — so the agent can run
untrusted suites without touching the host or the real default branch.

## Documentation

- [`docs/USAGE.md`](./docs/USAGE.md) — install, configure, commands, env vars, free-tier tips, extending tools, troubleshooting.
- [`docs/USECASES.md`](./docs/USECASES.md) — practical use cases and honest limits.
- [`MEMO.md`](./MEMO.md) — what was cut, future work, and the design decision I defend.

