"""GitHub REST client tested against a fake session (cassette).

Proves the transport concerns without network: success decoding, typed-error
mapping, and that a 429 is retried (rate-limit -> retryable) then succeeds.
"""
from types import SimpleNamespace as NS

import pytest

from patchwork.errors import ExternalServiceError
from patchwork.tools.github_api import GitHubClient


class FakeResp:
    def __init__(self, status_code, json_body=None, headers=None, text=""):
        self.status_code = status_code
        self._json = json_body or {}
        self.headers = headers or {}
        self.text = text
        self.content = b"x" if json_body is not None else b""

    def json(self):
        return self._json


class FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0
        self.headers = {}

    def request(self, method, url, timeout=None, **kwargs):
        self.calls += 1
        r = self._responses.pop(0)
        return r


def _client(responses):
    return GitHubClient("tok", session=FakeSession(responses))


def test_get_repo_decodes_json():
    c = _client([FakeResp(200, {"full_name": "o/r", "default_branch": "main"})])
    # call the tool-facing method directly
    d = c.get_repo("o", "r")
    assert d["default_branch"] == "main"


def test_4xx_maps_to_external_service_error():
    c = _client([FakeResp(404, text="not found")])
    with pytest.raises(ExternalServiceError) as ei:
        c.get_repo("o", "missing")
    assert ei.value.status == 404


def test_429_is_retried_then_succeeds():
    sess_responses = [
        FakeResp(429, headers={"Retry-After": "0"}),  # retry_after=0 -> instant
        FakeResp(200, {"full_name": "o/r"}),
    ]
    c = _client(sess_responses)
    d = c.get_repo("o", "r")
    assert d["full_name"] == "o/r"
    assert c._session.calls == 2  # one retry happened
