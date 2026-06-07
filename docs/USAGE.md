# Usage guide

## Install

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"          # core + tests (no LLM SDK needed to run tests)
# add the provider(s) you'll use:
pip install -e ".[anthropic]"    # Anthropic
pip install -e ".[groq]"         # Groq
pip install -e ".[gemini]"       # Gemini
pip install -e ".[all]"          # everything
```

`git` must be on PATH (the agent shells out to it in a sandbox).

## Configure

Copy `.env.example` → `.env` and set a provider key. Provider is chosen by
`PATCHWORK_LLM_PROVIDER`, or auto-detected from whichever key is present
(preference: Anthropic → Groq → Gemini).

```bash
patchwork doctor    # verify keys, git, provider, tool count
```

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `PATCHWORK_LLM_PROVIDER` | auto | `anthropic` \| `groq` \| `gemini` |
| `ANTHROPIC_API_KEY` / `PATCHWORK_ANTHROPIC_MODEL` | — / `claude-opus-4-8` | Anthropic |
| `GROQ_API_KEY` / `PATCHWORK_GROQ_MODEL` / `PATCHWORK_GROQ_RPM` | — / `llama-3.3-70b-versatile` / `30` | Groq |
| `GEMINI_API_KEY` / `PATCHWORK_GEMINI_MODEL` / `PATCHWORK_GEMINI_RPM` | — / `gemini-2.5-pro` / `10` | Gemini |
| `GITHUB_TOKEN` | — | required only to open PRs |
| `PATCHWORK_MAX_TOOL_CALLS` | `60` | per-session tool-call budget |
| `PATCHWORK_CONTEXT_TOKEN_BUDGET` | `120000` | transcript token budget before compaction |
| `PATCHWORK_CONTEXT_KEEP_RECENT` | `8` | recent messages kept verbatim during compaction |
| `PATCHWORK_DYNAMIC_TOOLS` | `false` | advertise only meta-tools + loaded tools (smaller requests; fits small free tiers) |
| `PATCHWORK_LOG_LEVEL` / `PATCHWORK_LOG_JSON` | `INFO` / `false` | logging |

## Commands

```bash
patchwork tools                       # list the 54 tools, grouped by namespace
patchwork doctor                      # environment / config check
patchwork run --repo PATH             # fix failing tests in a local repo
patchwork run --clone URL             # clone then fix
patchwork run --clone URL --open-pr --github-repo owner/repo
patchwork run --repo PATH --task "only fix tests in tests/unit"
```

Exit codes: `0` verified green, `2` ran but still red, `1` error.

### Watch it work

```bash
PATCHWORK_LOG_JSON=true patchwork run --repo ./fixtures/off_by_one 2>&1 | tee run.log
grep -c '"msg": "tool executed"' run.log    # tool calls in the session
```

## Evaluation

```bash
python -m patchwork.eval.harness     # pass@1 over seeded buggy fixtures (needs a key)
```

## Docker

```bash
docker build -t patchwork .
docker run --rm -e GROQ_API_KEY=... -e PATCHWORK_LLM_PROVIDER=groq \
  -v "$(pwd):/work" patchwork run --repo /work
```

## Free-tier tips (important)

Free LLM tiers cap request size / tokens-per-minute, and Patchwork sends all 54
tool schemas (~4.5k tokens) per call. To fit a small tier (e.g. Groq
`gpt-oss-120b`, ~8k/request):

```bash
PATCHWORK_GROQ_MODEL=openai/gpt-oss-120b
PATCHWORK_CONTEXT_TOKEN_BUDGET=3000
PATCHWORK_CONTEXT_KEEP_RECENT=4
PATCHWORK_DYNAMIC_TOOLS=true     # advertise only loaded tools — ~3x smaller requests
```

Small fixes run clean; larger multi-fix repos need a higher tier (or the planned
dynamic tool loading — see MEMO).

## Extending: add a tool

Tools are decorated functions; the registry derives the schema from the
signature and offers it to the model automatically — no dispatcher to edit.

```python
from patchwork.tools.base import tool, ToolContext

@tool(namespace="code", scope="read", descriptions={"path": "file"})
def todo_count(ctx: ToolContext, path: str) -> dict:
    """Count TODO markers in a file."""   # docstring = model-facing description
    text = ctx.sandbox.read(path)
    return {"path": path, "todos": text.count("TODO")}
```

Put it in a module listed in `_BUILTIN_MODULES` (`registry.py`). Use
`scope="write"` for anything that mutates, so read-only subagents can't reach it.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `No LLM provider available` | set a key or `PATCHWORK_LLM_PROVIDER` |
| `groq error 413` | request over the tier cap — lower `PATCHWORK_CONTEXT_TOKEN_BUDGET` and use a model with a larger cap |
| `gemini/groq rate limit` | free-tier TPM — lower `PATCHWORK_*_RPM`, or wait for the window to reset |
| run exits `2` (still red) | read `final_report` + `ledger`; the fix didn't hold — try a higher-tier model |
| `no GitHub client on context` | set `GITHUB_TOKEN` to open PRs |
