#!/usr/bin/env python3
"""Publish the result of an open-PR sweep on each pull request head commit."""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

API_ROOT = "https://api.github.com"
CHECK_NAME = "Open Plugin Contribution Gate"
USER_AGENT = "awesome-ai-plugins-open-pr-sweep"
REQUEST_TIMEOUT_SECONDS = 30
SCAN_JOB_RE = re.compile(r"Scan PR #(\d+) source")


def github_api(repository: str, path: str, token: str, *, method: str = "GET", payload: object | None = None) -> object:
    """Call the GitHub REST API with a bounded, JSON-only request."""

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": USER_AGENT,
    }
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = Request(
        f"{API_ROOT}/repos/{repository}{path}",
        headers=headers,
        method=method,
        data=data,
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"GitHub API {method} {path} failed: {error}") from error


def scan_conclusions(repository: str, run_id: str, token: str) -> dict[int, list[str]]:
    """Return scanner job conclusions grouped by pull request number."""

    conclusions: dict[int, list[str]] = {}
    page = 1
    while True:
        payload = github_api(repository, f"/actions/runs/{run_id}/jobs?per_page=100&page={page}", token)
        if not isinstance(payload, dict):
            raise RuntimeError("GitHub returned an invalid jobs response")
        jobs = payload.get("jobs")
        if not isinstance(jobs, list):
            raise RuntimeError("GitHub returned no jobs list")
        for job in jobs:
            if not isinstance(job, dict):
                continue
            name = job.get("name")
            conclusion = job.get("conclusion")
            if not isinstance(name, str) or not isinstance(conclusion, str):
                continue
            match = SCAN_JOB_RE.search(name)
            if match:
                conclusions.setdefault(int(match.group(1)), []).append(conclusion)
        if len(jobs) < 100:
            return conclusions
        page += 1


def check_summary(result: dict[str, object], scanner_jobs: dict[int, list[str]]) -> tuple[str, str, str]:
    """Map validator/scan output to a check conclusion, title, and summary."""

    number = result.get("pr_number")
    state = result.get("state")
    reasons = result.get("failure_reasons")
    if not isinstance(number, int) or not isinstance(state, str):
        raise RuntimeError("validator returned an invalid PR result")

    if state == "success":
        return (
            "success",
            "Contribution requirements passed",
            "No new Community Plugins entries require validation in this pull request.",
        )

    if state == "failure":
        failure_lines = reasons if isinstance(reasons, list) else []
        details = "\n".join(f"- {item}" for item in failure_lines if isinstance(item, str))
        return (
            "failure",
            "Contribution requirements failed",
            "Required scanner CI is missing or could not be validated.\n\n" + details,
        )

    if state == "scan":
        jobs = scanner_jobs.get(number, [])
        if jobs and all(conclusion == "success" for conclusion in jobs):
            return (
                "success",
                "Contribution scan passed",
                f"All {len(jobs)} source-repository scanner job(s) passed the contribution gate.",
            )
        job_details = ", ".join(jobs) if jobs else "no scanner job was recorded"
        return (
            "failure",
            "Contribution scan failed",
            f"One or more source-repository scanner jobs did not pass: {job_details}.",
        )

    raise RuntimeError(f"unknown validator result state: {state}")


def publish_check(
    repository: str,
    result: dict[str, object],
    scanner_jobs: dict[int, list[str]],
    run_url: str,
    token: str,
) -> None:
    """Create or update the check run for one pull request head commit."""

    head_sha = result.get("head_sha")
    number = result.get("pr_number")
    title = result.get("title")
    if not isinstance(head_sha, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", head_sha):
        raise RuntimeError(f"PR #{number} has an invalid head SHA")
    if not isinstance(number, int) or not isinstance(title, str):
        raise RuntimeError("validator returned an invalid PR identity")

    conclusion, check_title, summary = check_summary(result, scanner_jobs)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    payload = {
        "name": CHECK_NAME,
        "head_sha": head_sha,
        "status": "completed",
        "conclusion": conclusion,
        "started_at": now,
        "completed_at": now,
        "details_url": run_url,
        "output": {
            "title": check_title,
            "summary": f"PR #{number} — {title}\n\n{summary}",
        },
    }
    update_payload = {key: value for key, value in payload.items() if key != "head_sha"}

    existing = github_api(
        repository,
        f"/commits/{quote(head_sha, safe='')}/check-runs?check_name={quote(CHECK_NAME)}&per_page=100",
        token,
    )
    check_runs = existing.get("check_runs", []) if isinstance(existing, dict) else []
    matching = [
        item for item in check_runs if isinstance(item, dict) and item.get("name") == CHECK_NAME
    ]
    if matching and isinstance(matching[-1].get("id"), int):
        github_api(
            repository,
            f"/check-runs/{matching[-1]['id']}",
            token,
            method="PATCH",
            payload=update_payload,
        )
    else:
        github_api(repository, "/check-runs", token, method="POST", payload=payload)
    print(f"Published {CHECK_NAME} for PR #{number}: {conclusion}")


def main() -> int:
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    run_id = os.environ.get("GITHUB_RUN_ID", "").strip()
    run_url = os.environ.get("SWEEP_RUN_URL", "").strip()
    results_json = os.environ.get("OPEN_PR_RESULTS", "[]")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        print("ERROR: GITHUB_REPOSITORY must be an owner/repository pair", file=sys.stderr)
        return 1
    if not token or not run_id or not run_url:
        print("ERROR: GITHUB_TOKEN, GITHUB_RUN_ID, and SWEEP_RUN_URL are required", file=sys.stderr)
        return 1
    try:
        results = json.loads(results_json)
        if not isinstance(results, list):
            raise RuntimeError("OPEN_PR_RESULTS must be a JSON array")
        scanner_jobs = scan_conclusions(repository, run_id, token)
        for result in results:
            if not isinstance(result, dict):
                raise RuntimeError("validator returned a non-object PR result")
            publish_check(repository, result, scanner_jobs, run_url, token)
    except (RuntimeError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
