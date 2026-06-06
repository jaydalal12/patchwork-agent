"""A confined working copy of the target repository.

Every git/code/ci tool operates *inside* one ``RepoSandbox``: an isolated
directory clone of the target repo. This is the safety boundary — the agent
runs untrusted test suites and edits files, so it must never touch anything
outside this directory or push to the real default branch. Path access is
confined; commands run with a timeout.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from patchwork.errors import ToolExecutionError
from patchwork.observability import get_logger

_log = get_logger("sandbox")


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class RepoSandbox:
    def __init__(self, workdir: Path):
        self.workdir = workdir.resolve()
        if not self.workdir.exists():
            raise ToolExecutionError(f"sandbox workdir does not exist: {self.workdir}")

    # -- construction -----------------------------------------------------
    @classmethod
    def from_local(cls, src: Path, root: Path) -> "RepoSandbox":
        root.mkdir(parents=True, exist_ok=True)
        dest = Path(tempfile.mkdtemp(prefix="repo_", dir=str(root)))
        # Copy tree but skip the original .git to start clean, then init fresh.
        shutil.copytree(src, dest, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(".git", "__pycache__", ".venv", "node_modules"))
        sb = cls(dest)
        if not (dest / ".git").exists():
            sb.run_git(["init", "-q"])
            sb.run_git(["add", "-A"])
            sb.run_git(["-c", "user.email=patchwork@local", "-c", "user.name=patchwork",
                        "commit", "-q", "-m", "baseline"])
        _log.info("sandbox created from local", src=str(src), dest=str(dest))
        return sb

    @classmethod
    def from_clone(cls, clone_url: str, root: Path, ref: Optional[str] = None) -> "RepoSandbox":
        root.mkdir(parents=True, exist_ok=True)
        dest = Path(tempfile.mkdtemp(prefix="repo_", dir=str(root)))
        args = ["clone", "--depth", "50", clone_url, str(dest)]
        r = _run(["git", *args], cwd=root, timeout=300)
        if not r.ok:
            raise ToolExecutionError(f"git clone failed: {r.stderr[:400]}")
        sb = cls(dest)
        if ref:
            sb.run_git(["checkout", ref])
        _log.info("sandbox created from clone", url=clone_url, dest=str(dest))
        return sb

    # -- path safety ------------------------------------------------------
    def resolve(self, rel: str) -> Path:
        p = (self.workdir / rel).resolve()
        if self.workdir not in p.parents and p != self.workdir:
            raise ToolExecutionError(f"path escapes sandbox: {rel}")
        return p

    # -- io ---------------------------------------------------------------
    def read(self, rel: str) -> str:
        p = self.resolve(rel)
        if not p.is_file():
            raise ToolExecutionError(f"not a file: {rel}")
        return p.read_text(encoding="utf-8", errors="replace")

    def write(self, rel: str, content: str) -> None:
        p = self.resolve(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def exists(self, rel: str) -> bool:
        try:
            return self.resolve(rel).exists()
        except ToolExecutionError:
            return False

    def list_files(self, subdir: str = ".", pattern: str = "*") -> List[str]:
        base = self.resolve(subdir)
        if not base.exists():
            return []
        return sorted(
            str(p.relative_to(self.workdir))
            for p in base.rglob(pattern)
            if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts
        )

    # -- process ----------------------------------------------------------
    def run(self, cmd: List[str], timeout: int = 120) -> RunResult:
        return _run(cmd, cwd=self.workdir, timeout=timeout)

    def run_git(self, args: List[str], timeout: int = 60) -> RunResult:
        r = _run(["git", *args], cwd=self.workdir, timeout=timeout)
        return r

    def cleanup(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)


def _run(cmd: List[str], cwd: Path, timeout: int) -> RunResult:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        return RunResult(proc.returncode, proc.stdout, proc.stderr)
    except subprocess.TimeoutExpired as e:
        raise ToolExecutionError(f"command timed out after {timeout}s: {' '.join(cmd)}") from e
    except FileNotFoundError as e:
        raise ToolExecutionError(f"command not found: {cmd[0]}") from e
