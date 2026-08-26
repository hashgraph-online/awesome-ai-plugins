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
COMMENT_MARKER = "<!-- awesome-ai-plugins-contribution-gate -->"
USER_AGENT = "awesome-ai-plugins-open-pr-sweep"
REQUEST_TIMEOUT_SECONDS = 30
SCAN_JOB_RE = re.compile(r"Scan PR (?:#(\d+) source|\((\d+),)")
GITHUB_LOGIN_RE = re.compile(r"^[A-Za-z0-9-]{1,39}$")


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
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace").strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"GitHub API {method} {path} failed: HTTP {error.code}{suffix}") from error
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
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
                number = match.group(1) or match.group(2)
                conclusions.setdefault(int(number), []).append(conclusion)
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
            "Catalog validation failed.\n\n" + details,
        )

    if state == "scan":
        jobs = scanner_jobs.get(number, [])
        if not jobs:
            return (
                "success",
                "scan unavailable",
                "HOL centralized scan could not run. Catalog validation passed; scanner results are advisory.",
            )
        if all(conclusion == "success" for conclusion in jobs):
            return (
                "success",
                "scan passed",
                f"All {len(jobs)} source-repository scanner job(s) passed. Catalog validation passed.",
            )
        job_details = ", ".join(jobs)
        return (
            "success",
            "scan findings",
            "HOL centralized scan reported findings or did not pass: "
            f"{job_details}. This is advisory and does not block listing.",
        )

    raise RuntimeError(f"unknown validator result state: {state}")


def list_issue_comments(repository: str, number: int, token: str) -> list[dict[str, object]]:
    """Return all issue comments for a pull request."""

    comments: list[dict[str, object]] = []
    page = 1
    while True:
        payload = github_api(
            repository,
            f"/issues/{number}/comments?per_page=100&page={page}",
            token,
        )
        if not isinstance(payload, list):
            raise RuntimeError(f"GitHub returned an invalid comments response for PR #{number}")
        page_comments = [item for item in payload if isinstance(item, dict)]
        comments.extend(page_comments)
        if len(payload) < 100:
            return comments
        page += 1


def missing_scanner_ci_repos(result: dict[str, object]) -> list[str]:
    """Return owner/repo pairs whose source repository has no scanner CI."""

    contributions = result.get("contributions")
    if not isinstance(contributions, list):
        return []
    missing: list[str] = []
    for item in contributions:
        if not isinstance(item, dict):
            continue
        if item.get("scanner_ci") != "not_detected":
            continue
        owner = item.get("owner")
        repo = item.get("repo")
        if isinstance(owner, str) and isinstance(repo, str) and owner and repo:
            missing.append(f"{owner}/{repo}")
    return missing


def optional_scanner_ci_guidance(repos: list[str]) -> str:
    """Explain that scanner CI is optional and why maintainers should still add it."""

    listed = ", ".join(f"`{item}`" for item in repos)
    subject = listed if listed else "the source repository"
    return (
        "\n\n### Recommended: add scanner CI for security\n"
        f"This listing can merge without it. HOL still scans {subject} independently.\n\n"
        "We **recommend including** `hashgraph-online/ai-plugin-scanner-action` under "
        "`.github/workflows/` on `push` and `pull_request`. Continuous scanning keeps "
        "this catalog safer for MCP servers, skills, plugins, and other agent "
        "extensions people install from HOL.\n\n"
        "Adding it:\n"
        "- protects the ecosystem by catching secrets, dangerous hooks, and "
        "supply-chain issues before they ship;\n"
        "- keeps the listing at the **full trust score** (without maintainer CI it stays "
        "eligible, with a 10% trust-score reduction);\n"
        "- can surface findings in GitHub code scanning.\n\n"
        "See [CONTRIBUTING.md](https://github.com/hashgraph-online/awesome-ai-plugins/blob/main/CONTRIBUTING.md) "
        "and [SCANNER_GUIDE.md](https://github.com/hashgraph-online/awesome-ai-plugins/blob/main/SCANNER_GUIDE.md).\n"
    )


def remediation_comment(
    result: dict[str, object],
    conclusion: str,
    check_title: str,
    summary: str,
    run_url: str,
) -> str:
    """Build an idempotent contributor-facing remediation comment."""

    author_login = result.get("author_login")
    mention = f"@{author_login}" if isinstance(author_login, str) and GITHUB_LOGIN_RE.fullmatch(author_login) else "the contributor"
    if conclusion == "success":
        optional_guidance = ""
        missing = missing_scanner_ci_repos(result)
        if missing:
            optional_guidance += optional_scanner_ci_guidance(missing)
        if "scan findings" in check_title:
            optional_guidance += (
                "\nHOL's centralized scan reported findings. This does not block listing.\n"
            )
        elif "scan unavailable" in check_title:
            optional_guidance += (
                "\nHOL's centralized scan could not run. This does not block listing.\n"
            )
        return (
            f"{COMMENT_MARKER}\n\n"
            f"✅ **Contribution check passed.** {mention}, catalog validation succeeded.\n\n"
            f"### {check_title}\n{summary}{optional_guidance}\n"
            f"[View the latest sweep]({run_url})."
        )

    guidance = (
        "1. Use the Community Plugins format: "
        "`- [Name](https://github.com/owner/repo) - description`.\n"
        "2. Add the entry to the correct section, alphabetically, without duplicates."
    )

    return f"""{COMMENT_MARKER}

{mention} — this pull request needs updates before it can be merged.

### {check_title}
{summary}

### How to fix it
{guidance}

See the repository's [contribution requirements](https://github.com/hashgraph-online/awesome-ai-plugins/blob/main/CONTRIBUTING.md).

After pushing the changes, this check and comment will update automatically: {run_url}
"""


def upsert_remediation_comment(
    repository: str,
    result: dict[str, object],
    conclusion: str,
    check_title: str,
    summary: str,
    run_url: str,
    token: str,
) -> None:
    """Create or update the single contribution-gate comment for a PR."""

    number = result.get("pr_number")
    if not isinstance(number, int):
        raise RuntimeError("validator returned an invalid PR number")
    comments = list_issue_comments(repository, number, token)
    existing = next(
        (
            comment
            for comment in comments
            if isinstance(comment.get("body"), str) and COMMENT_MARKER in comment["body"]
        ),
        None,
    )
    if (
        conclusion == "success"
        and existing is None
        and not missing_scanner_ci_repos(result)
    ):
        return

    body = remediation_comment(result, conclusion, check_title, summary, run_url)
    if existing is not None and isinstance(existing.get("id"), int):
        github_api(
            repository,
            f"/issues/comments/{existing['id']}",
            token,
            method="PATCH",
            payload={"body": body},
        )
        print(f"Updated contribution-gate comment for PR #{number}")
        return

    github_api(
        repository,
        f"/issues/{number}/comments",
        token,
        method="POST",
        payload={"body": body},
    )
    print(f"Posted contribution-gate comment for PR #{number}")


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
    upsert_remediation_comment(repository, result, conclusion, check_title, summary, run_url, token)


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
