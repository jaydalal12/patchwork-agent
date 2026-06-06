"""Thin GitHub REST client: rate-limited, retried, typed.

No ``gh`` CLI dependency — just the REST API over ``requests``. Every call goes
through the shared limiter and retry policy, and maps HTTP failures onto our
typed errors. Tools call these methods; this class owns the transport concerns.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from patchwork.errors import ExternalServiceError, RateLimitError, TransientServiceError
from patchwork.observability import get_logger
from patchwork.resilience import RateLimiter
from patchwork.resilience.retry import BackoffPolicy, retry_call

_log = get_logger("github")
_API = "https://api.github.com"


class GitHubClient:
    def __init__(self, token: str, *, session: Any = None):
        import requests

        self._requests = requests
        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "patchwork-agent",
            }
        )
        # GitHub allows ~5000 req/hr authenticated; cap bursts well under that.
        self._limiter = RateLimiter(rate=10.0, burst=20)

    def _request(self, method: str, path: str, **kwargs) -> Any:
        self._limiter.acquire()
        url = path if path.startswith("http") else f"{_API}{path}"

        def do():
            resp = self._session.request(method, url, timeout=30, **kwargs)
            if resp.status_code == 429 or (
                resp.status_code == 403 and resp.headers.get("X-RateLimit-Remaining") == "0"
            ):
                retry_after = resp.headers.get("Retry-After")
                reset = resp.headers.get("X-RateLimit-Reset")
                hint = float(retry_after) if retry_after else None
                raise RateLimitError("github rate limit", retry_after=hint, status=resp.status_code)
            if 500 <= resp.status_code < 600:
                raise TransientServiceError(f"github {resp.status_code}", status=resp.status_code)
            if resp.status_code >= 400:
                raise ExternalServiceError(
                    f"github {resp.status_code}: {resp.text[:300]}", status=resp.status_code
                )
            return resp.json() if resp.content else {}

        return retry_call(do, policy=BackoffPolicy(max_attempts=4), op_name=f"github.{method} {path}")

    # -- a small slice of the API the tools need --------------------------
    def get_repo(self, owner: str, repo: str) -> Dict[str, Any]:
        return self._request("GET", f"/repos/{owner}/{repo}")

    def list_issues(self, owner: str, repo: str, state: str = "open") -> List[Dict[str, Any]]:
        return self._request("GET", f"/repos/{owner}/{repo}/issues", params={"state": state})

    def get_issue(self, owner: str, repo: str, number: int) -> Dict[str, Any]:
        return self._request("GET", f"/repos/{owner}/{repo}/issues/{number}")

    def list_pulls(self, owner: str, repo: str, state: str = "open") -> List[Dict[str, Any]]:
        return self._request("GET", f"/repos/{owner}/{repo}/pulls", params={"state": state})

    def get_pull(self, owner: str, repo: str, number: int) -> Dict[str, Any]:
        return self._request("GET", f"/repos/{owner}/{repo}/pulls/{number}")

    def create_pull(self, owner: str, repo: str, *, title: str, head: str, base: str, body: str) -> Dict[str, Any]:
        return self._request(
            "POST",
            f"/repos/{owner}/{repo}/pulls",
            json={"title": title, "head": head, "base": base, "body": body},
        )

    def create_issue_comment(self, owner: str, repo: str, number: int, body: str) -> Dict[str, Any]:
        return self._request(
            "POST", f"/repos/{owner}/{repo}/issues/{number}/comments", json={"body": body}
        )

    def list_check_runs(self, owner: str, repo: str, ref: str) -> Dict[str, Any]:
        return self._request("GET", f"/repos/{owner}/{repo}/commits/{ref}/check-runs")

    def list_workflow_runs(self, owner: str, repo: str) -> Dict[str, Any]:
        return self._request("GET", f"/repos/{owner}/{repo}/actions/runs", params={"per_page": 20})

    def get_contents(self, owner: str, repo: str, path: str, ref: Optional[str] = None) -> Any:
        params = {"ref": ref} if ref else None
        return self._request("GET", f"/repos/{owner}/{repo}/contents/{path}", params=params)

    def compare(self, owner: str, repo: str, base: str, head: str) -> Dict[str, Any]:
        return self._request("GET", f"/repos/{owner}/{repo}/compare/{base}...{head}")
