"""``code.*`` tools — read, search, and edit source in the sandbox.

Edits are surgical (line ranges, string replacement, unified-diff apply) rather
than blind whole-file writes, so the agent's changes stay reviewable and the
diff stays small.
"""
from __future__ import annotations

import re
from typing import List

from patchwork.errors import ToolExecutionError
from patchwork.tools.base import ToolContext, tool


def _sb(ctx: ToolContext):
    if ctx.sandbox is None:
        raise ToolExecutionError("no sandbox on context")
    return ctx.sandbox


@tool(namespace="code", scope="read", descriptions={"path": "file path relative to repo root"})
def read_file(ctx: ToolContext, path: str) -> str:
    """Read an entire text file from the repository."""
    return _sb(ctx).read(path)


@tool(namespace="code", scope="read",
      descriptions={"path": "file path", "start": "1-based start line", "end": "inclusive end line"})
def read_lines(ctx: ToolContext, path: str, start: int, end: int) -> dict:
    """Read a specific 1-based, inclusive line range of a file."""
    lines = _sb(ctx).read(path).splitlines()
    start = max(1, start)
    end = min(len(lines), end)
    chunk = lines[start - 1:end]
    return {"path": path, "start": start, "end": end, "text": "\n".join(chunk)}


@tool(namespace="code", scope="write", descriptions={"path": "file path", "content": "full new file content"})
def write_file(ctx: ToolContext, path: str, content: str) -> dict:
    """Overwrite a file with new content (creates parent dirs as needed)."""
    _sb(ctx).write(path, content)
    return {"path": path, "bytes": len(content)}


@tool(namespace="code", scope="write",
      descriptions={"path": "file path", "old": "exact text to replace", "new": "replacement text"})
def replace_in_file(ctx: ToolContext, path: str, old: str, new: str) -> dict:
    """Replace the first exact occurrence of a string in a file."""
    sb = _sb(ctx)
    content = sb.read(path)
    if old not in content:
        raise ToolExecutionError(f"'old' text not found in {path}")
    count = content.count(old)
    sb.write(path, content.replace(old, new, 1))
    return {"path": path, "replaced": 1, "remaining_occurrences": count - 1}


@tool(namespace="code", scope="write",
      descriptions={"path": "file", "start": "1-based start", "end": "inclusive end", "text": "replacement block"})
def replace_lines(ctx: ToolContext, path: str, start: int, end: int, text: str) -> dict:
    """Replace a 1-based inclusive line range with new text."""
    sb = _sb(ctx)
    lines = sb.read(path).splitlines()
    if start < 1 or end > len(lines) or start > end:
        raise ToolExecutionError(f"invalid range {start}-{end} for {len(lines)}-line file")
    new_lines = lines[:start - 1] + text.splitlines() + lines[end:]
    sb.write(path, "\n".join(new_lines) + "\n")
    return {"path": path, "replaced_lines": end - start + 1}


@tool(namespace="code", scope="write", descriptions={"patch": "a unified diff to apply with `git apply`"})
def apply_patch(ctx: ToolContext, patch: str) -> dict:
    """Apply a unified diff to the working tree via `git apply`."""
    sb = _sb(ctx)
    patch_path = sb.workdir / ".patchwork.patch"
    patch_path.write_text(patch if patch.endswith("\n") else patch + "\n")
    r = sb.run_git(["apply", "--whitespace=nowarn", str(patch_path)])
    patch_path.unlink(missing_ok=True)
    if not r.ok:
        raise ToolExecutionError(f"git apply failed: {r.stderr.strip()[:400]}")
    return {"applied": True}


@tool(namespace="code", scope="read",
      descriptions={"pattern": "regex", "glob": "filename glob to limit search"})
def search(ctx: ToolContext, pattern: str, glob: str = "*.py") -> dict:
    """Regex-search files (like grep). Returns matches with file:line."""
    sb = _sb(ctx)
    try:
        rx = re.compile(pattern)
    except re.error as e:
        raise ToolExecutionError(f"bad regex: {e}")
    hits = []
    for rel in sb.list_files(".", glob):
        try:
            for i, line in enumerate(sb.read(rel).splitlines(), 1):
                if rx.search(line):
                    hits.append({"file": rel, "line": i, "text": line.strip()[:200]})
                    if len(hits) >= 200:
                        return {"count": len(hits), "matches": hits, "truncated": True}
        except ToolExecutionError:
            continue
    return {"count": len(hits), "matches": hits, "truncated": False}


@tool(namespace="code", scope="read", descriptions={"subdir": "directory to list", "glob": "filename glob"})
def list_files(ctx: ToolContext, subdir: str = ".", glob: str = "*") -> dict:
    """List files under a directory matching a glob."""
    files = _sb(ctx).list_files(subdir, glob)
    return {"count": len(files), "files": files[:500]}


@tool(namespace="code", scope="read", descriptions={"name": "function or class name"})
def find_definition(ctx: ToolContext, name: str) -> dict:
    """Locate where a function or class is defined."""
    return search(ctx, pattern=rf"^\s*(def|class)\s+{re.escape(name)}\b", glob="*.py")


@tool(namespace="code", scope="read", descriptions={"symbol": "identifier to find usages of"})
def find_references(ctx: ToolContext, symbol: str) -> dict:
    """Find usages of an identifier across the Python sources."""
    return search(ctx, pattern=rf"\b{re.escape(symbol)}\b", glob="*.py")


@tool(namespace="code", scope="read", descriptions={"path": "python file"})
def file_outline(ctx: ToolContext, path: str) -> dict:
    """List the top-level functions and classes (with line numbers) in a file."""
    outline = []
    for i, line in enumerate(_sb(ctx).read(path).splitlines(), 1):
        m = re.match(r"^(def|class)\s+(\w+)", line)
        if m:
            outline.append({"kind": m.group(1), "name": m.group(2), "line": i})
    return {"path": path, "symbols": outline}


@tool(namespace="code", scope="read", descriptions={"path": "file path"})
def count_lines(ctx: ToolContext, path: str) -> dict:
    """Return the number of lines in a file (cheap way to size a file before reading)."""
    return {"path": path, "lines": len(_sb(ctx).read(path).splitlines())}


@tool(namespace="code", scope="read")
def list_dir(ctx: ToolContext, subdir: str = ".") -> dict:
    """List immediate entries (files and dirs) of a directory."""
    base = _sb(ctx).resolve(subdir)
    if not base.is_dir():
        raise ToolExecutionError(f"not a directory: {subdir}")
    entries = sorted(
        (p.name + ("/" if p.is_dir() else "")) for p in base.iterdir() if p.name != ".git"
    )
    return {"subdir": subdir, "entries": entries}
