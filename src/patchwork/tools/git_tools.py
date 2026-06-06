"""``git.*`` tools — version control over the sandbox working copy.

All operate on ``ctx.sandbox`` and shell out to git. Mutating tools are
``scope="write"`` so a read-only subagent cannot reach them.
"""
from __future__ import annotations

from typing import List

from patchwork.errors import ToolExecutionError
from patchwork.tools.base import ToolContext, tool

_GIT_ID = ["-c", "user.email=patchwork@local", "-c", "user.name=Patchwork Agent"]


def _sb(ctx: ToolContext):
    if ctx.sandbox is None:
        raise ToolExecutionError("no sandbox on context")
    return ctx.sandbox


@tool(namespace="git", scope="read")
def status(ctx: ToolContext) -> dict:
    """Show the working-tree status (porcelain) of the repository."""
    r = _sb(ctx).run_git(["status", "--porcelain=v1", "-b"])
    return {"clean": r.stdout.strip().count("\n") <= 0, "status": r.stdout}


@tool(namespace="git", scope="read")
def current_branch(ctx: ToolContext) -> dict:
    """Return the name of the currently checked-out branch."""
    r = _sb(ctx).run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    return {"branch": r.stdout.strip()}


@tool(namespace="git", scope="read")
def list_branches(ctx: ToolContext) -> dict:
    """List all local branches."""
    r = _sb(ctx).run_git(["branch", "--format=%(refname:short)"])
    return {"branches": [b for b in r.stdout.splitlines() if b]}


@tool(namespace="git", scope="write", descriptions={"name": "new branch name"})
def create_branch(ctx: ToolContext, name: str) -> dict:
    """Create and check out a new branch from the current HEAD."""
    r = _sb(ctx).run_git(["checkout", "-b", name])
    if not r.ok:
        raise ToolExecutionError(f"create_branch failed: {r.stderr.strip()}")
    return {"branch": name, "created": True}


@tool(namespace="git", scope="write", descriptions={"ref": "branch, tag, or commit"})
def checkout(ctx: ToolContext, ref: str) -> dict:
    """Check out an existing branch, tag, or commit."""
    r = _sb(ctx).run_git(["checkout", ref])
    if not r.ok:
        raise ToolExecutionError(f"checkout failed: {r.stderr.strip()}")
    return {"ref": ref, "ok": True}


@tool(namespace="git", scope="read", descriptions={"path": "optional file to limit the diff to"})
def diff(ctx: ToolContext, path: str = "") -> str:
    """Show the unstaged diff, optionally limited to a single path."""
    args = ["diff"]
    if path:
        args += ["--", path]
    return _sb(ctx).run_git(args).stdout or "(no changes)"


@tool(namespace="git", scope="read")
def diff_stat(ctx: ToolContext) -> str:
    """Show a summary (files changed, insertions/deletions) of pending changes."""
    return _sb(ctx).run_git(["diff", "--stat", "HEAD"]).stdout or "(no changes)"


@tool(namespace="git", scope="read")
def staged_diff(ctx: ToolContext) -> str:
    """Show the diff of staged (added) changes."""
    return _sb(ctx).run_git(["diff", "--cached"]).stdout or "(nothing staged)"


@tool(namespace="git", scope="write", descriptions={"paths": "files to stage; empty = all"})
def add(ctx: ToolContext, paths: List[str] = []) -> dict:
    """Stage files for commit. With no paths, stages everything."""
    args = ["add"] + (paths if paths else ["-A"])
    r = _sb(ctx).run_git(args)
    if not r.ok:
        raise ToolExecutionError(f"add failed: {r.stderr.strip()}")
    return {"staged": paths or "all"}


@tool(namespace="git", scope="write", descriptions={"message": "commit message"})
def commit(ctx: ToolContext, message: str) -> dict:
    """Commit staged changes with the given message."""
    r = _sb(ctx).run_git([*_GIT_ID, "commit", "-m", message])
    if not r.ok:
        raise ToolExecutionError(f"commit failed: {r.stderr.strip() or r.stdout.strip()}")
    sha = _sb(ctx).run_git(["rev-parse", "HEAD"]).stdout.strip()
    return {"committed": True, "sha": sha[:10], "message": message}


@tool(namespace="git", scope="read", descriptions={"limit": "number of commits"})
def log(ctx: ToolContext, limit: int = 10) -> dict:
    """Show recent commit history (sha + subject)."""
    r = _sb(ctx).run_git(["log", f"-{limit}", "--pretty=%h %s"])
    return {"commits": r.stdout.splitlines()}


@tool(namespace="git", scope="read", descriptions={"path": "file to blame"})
def blame(ctx: ToolContext, path: str) -> str:
    """Show line-by-line last-modification info for a file."""
    return _sb(ctx).run_git(["blame", "--date=short", path]).stdout[:6000]


@tool(namespace="git", scope="write", descriptions={"path": "file to revert to HEAD"})
def restore_file(ctx: ToolContext, path: str) -> dict:
    """Discard working-tree changes to a file (restore it from HEAD)."""
    r = _sb(ctx).run_git(["checkout", "HEAD", "--", path])
    if not r.ok:
        raise ToolExecutionError(f"restore failed: {r.stderr.strip()}")
    return {"restored": path}


@tool(namespace="git", scope="read", descriptions={"ref": "revision to read from", "path": "file path"})
def show_at(ctx: ToolContext, ref: str, path: str) -> str:
    """Show the contents of a file at a specific revision."""
    return _sb(ctx).run_git(["show", f"{ref}:{path}"]).stdout


@tool(namespace="git", scope="read")
def changed_files(ctx: ToolContext) -> dict:
    """List the names of files changed in the working tree relative to HEAD."""
    r = _sb(ctx).run_git(["diff", "--name-only", "HEAD"])
    return {"files": [f for f in r.stdout.splitlines() if f]}
