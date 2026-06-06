"""``github.*`` tools — interact with the remote via the REST client.

Read tools work with just a token; the one write tool (``open_pull_request``)
is the agent's outward-facing action and is gated ``scope="write"``.
``parse_repo_url`` is pure (no network) so the agent can normalize input first.
"""
from __future__ import annotations

import re

from patchwork.errors import ToolExecutionError
from patchwork.tools.base import ToolContext, tool


def _gh(ctx: ToolContext):
    if ctx.github is None:
        raise ToolExecutionError("no GitHub client on context (is GITHUB_TOKEN set?)")
    return ctx.github


@tool(namespace="github", scope="read", descriptions={"url": "a github repo URL or owner/repo string"})
def parse_repo_url(ctx: ToolContext, url: str) -> dict:
    """Parse a GitHub URL or 'owner/repo' string into owner and repo. No network."""
    m = re.search(r"(?:github\.com[:/])?([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", url.strip())
    if not m:
        raise ToolExecutionError(f"cannot parse repo from: {url}")
    return {"owner": m.group(1), "repo": m.group(2)}


@tool(namespace="github", scope="read", descriptions={"owner": "repo owner", "repo": "repo name"})
def get_repo(ctx: ToolContext, owner: str, repo: str) -> dict:
    """Fetch repository metadata (default branch, description, language)."""
    d = _gh(ctx).get_repo(owner, repo)
    return {
        "full_name": d.get("full_name"),
        "default_branch": d.get("default_branch"),
        "language": d.get("language"),
        "open_issues": d.get("open_issues_count"),
        "clone_url": d.get("clone_url"),
    }


@tool(namespace="github", scope="read")
def list_issues(ctx: ToolContext, owner: str, repo: str, state: str = "open") -> dict:
    """List issues for a repository."""
    items = _gh(ctx).list_issues(owner, repo, state)
    return {"count": len(items),
            "issues": [{"number": i["number"], "title": i["title"]} for i in items if "pull_request" not in i]}


@tool(namespace="github", scope="read")
def get_issue(ctx: ToolContext, owner: str, repo: str, number: int) -> dict:
    """Fetch one issue's title and body."""
    i = _gh(ctx).get_issue(owner, repo, number)
    return {"number": i["number"], "title": i["title"], "body": (i.get("body") or "")[:4000], "state": i["state"]}


@tool(namespace="github", scope="read")
def list_pull_requests(ctx: ToolContext, owner: str, repo: str, state: str = "open") -> dict:
    """List pull requests for a repository."""
    items = _gh(ctx).list_pulls(owner, repo, state)
    return {"count": len(items), "pulls": [{"number": p["number"], "title": p["title"], "head": p["head"]["ref"]} for p in items]}


@tool(namespace="github", scope="read")
def get_pull_request(ctx: ToolContext, owner: str, repo: str, number: int) -> dict:
    """Fetch one pull request's metadata (title, state, branches, mergeability)."""
    p = _gh(ctx).get_pull(owner, repo, number)
    return {"number": p["number"], "title": p["title"], "state": p["state"],
            "head": p["head"]["ref"], "base": p["base"]["ref"], "mergeable": p.get("mergeable")}


@tool(namespace="github", scope="read")
def list_check_runs(ctx: ToolContext, owner: str, repo: str, ref: str) -> dict:
    """List CI check-run conclusions for a commit ref."""
    d = _gh(ctx).list_check_runs(owner, repo, ref)
    runs = d.get("check_runs", [])
    return {"count": len(runs),
            "checks": [{"name": r["name"], "status": r["status"], "conclusion": r.get("conclusion")} for r in runs]}


@tool(namespace="github", scope="read")
def list_workflow_runs(ctx: ToolContext, owner: str, repo: str) -> dict:
    """List recent GitHub Actions workflow runs and their conclusions."""
    d = _gh(ctx).list_workflow_runs(owner, repo)
    runs = d.get("workflow_runs", [])
    return {"count": len(runs),
            "runs": [{"name": r["name"], "status": r["status"], "conclusion": r.get("conclusion"), "head": r["head_branch"]} for r in runs[:15]]}


@tool(namespace="github", scope="read")
def get_file_contents(ctx: ToolContext, owner: str, repo: str, path: str, ref: str = "") -> dict:
    """Fetch a file's contents from the remote repository."""
    import base64
    d = _gh(ctx).get_contents(owner, repo, path, ref or None)
    if isinstance(d, list):
        return {"path": path, "is_dir": True, "entries": [e["name"] for e in d]}
    content = base64.b64decode(d.get("content", "")).decode("utf-8", "replace") if d.get("content") else ""
    return {"path": path, "is_dir": False, "content": content[:8000]}


@tool(namespace="github", scope="read")
def compare_refs(ctx: ToolContext, owner: str, repo: str, base: str, head: str) -> dict:
    """Compare two refs and summarize the changed files."""
    d = _gh(ctx).compare(owner, repo, base, head)
    return {"status": d.get("status"), "ahead_by": d.get("ahead_by"),
            "files": [f["filename"] for f in d.get("files", [])][:50]}


@tool(namespace="github", scope="read")
def rate_limit(ctx: ToolContext) -> dict:
    """Report the current GitHub API rate-limit budget."""
    d = _gh(ctx)._request("GET", "/rate_limit")
    core = d.get("resources", {}).get("core", {})
    return {"remaining": core.get("remaining"), "limit": core.get("limit")}


@tool(namespace="github", scope="write",
      descriptions={"title": "PR title", "head": "source branch", "base": "target branch", "body": "PR description"})
def open_pull_request(ctx: ToolContext, owner: str, repo: str, title: str, head: str, base: str, body: str) -> dict:
    """Open a pull request. The agent's outward-facing action — gated as write."""
    d = _gh(ctx).create_pull(owner, repo, title=title, head=head, base=base, body=body)
    return {"number": d.get("number"), "url": d.get("html_url"), "state": d.get("state")}


@tool(namespace="github", scope="write")
def comment_on_issue(ctx: ToolContext, owner: str, repo: str, number: int, body: str) -> dict:
    """Post a comment on an issue or pull request."""
    d = _gh(ctx).create_issue_comment(owner, repo, number, body)
    return {"id": d.get("id"), "url": d.get("html_url")}
