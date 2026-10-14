"""Small GitHub Issues state store with revision checks and safe errors."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .engine import canonical_json, validate_state


STATE_MARKER = "<!-- finmentor-monitor-state-v1 -->"
BACKUP_MARKER = "<!-- finmentor-backup-status-v1 -->"
MAX_BODY_BYTES = 200_000


class StoreError(RuntimeError):
    pass


def render_json_body(marker: str, value: dict[str, Any]) -> str:
    return f"{marker}\n```json\n{json.dumps(value, indent=2, sort_keys=True)}\n```\n"


def parse_json_body(body: object, marker: str) -> dict[str, Any]:
    if not isinstance(body, str) or marker not in body or len(body.encode("utf-8")) > MAX_BODY_BYTES:
        raise StoreError("state body is missing or invalid")
    match = re.search(r"```json\s*(\{.*?\})\s*```", body, flags=re.DOTALL)
    if not match:
        raise StoreError("state JSON block is missing")
    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise StoreError("state JSON is invalid") from exc
    if not isinstance(value, dict):
        raise StoreError("state JSON must be an object")
    return value


@dataclass
class GitHubIssueClient:
    repository: str
    token: str
    api_url: str = "https://api.github.com"

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repository):
            raise StoreError("repository is invalid")
        if not self.token:
            raise StoreError("GitHub token is unavailable")

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.api_url}{path}",
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "finmentor-staging-monitor",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise StoreError(f"GitHub API {method} failed: {type(exc).__name__}") from None
        if not isinstance(result, dict):
            raise StoreError("GitHub API response is invalid")
        return result

    def read_issue(self, issue_number: int) -> dict[str, Any]:
        return self._request("GET", f"/repos/{self.repository}/issues/{int(issue_number)}")

    def update_issue_body(self, issue_number: int, body: str) -> None:
        self._request(
            "PATCH",
            f"/repos/{self.repository}/issues/{int(issue_number)}",
            {"body": body},
        )


class IncidentStateStore:
    def __init__(self, client: GitHubIssueClient, issue_number: int) -> None:
        self.client = client
        self.issue_number = issue_number

    def load(self) -> dict[str, Any]:
        issue = self.client.read_issue(self.issue_number)
        state = parse_json_body(issue.get("body"), STATE_MARKER)
        return validate_state(state)

    def save(self, state: dict[str, Any], *, expected_revision: int) -> None:
        validate_state(state)
        current = self.load()
        if current["revision"] != expected_revision:
            raise StoreError("incident state changed concurrently")
        state["revision"] = expected_revision + 1
        self.client.update_issue_body(
            self.issue_number, render_json_body(STATE_MARKER, state)
        )


def state_digest(state: dict[str, Any]) -> str:
    """Useful for tests without logging or publishing the state payload."""

    import hashlib

    return hashlib.sha256(canonical_json(state).encode("utf-8")).hexdigest()
