#!/usr/bin/env python3
"""Re-render legacy contribution-gate comments on closed pull requests.

The open-PR sweep keeps comments on open pull requests current. Closed pull
requests are never swept again, so their gate comments keep whatever format was
current when they closed. This script rebuilds each comment from the PR's exact
base/head commits and its latest retained scan artifact, then edits it in place.
"""

from __future__ import annotations

import difflib
import importlib.util
import io
import json
import os
import re
import sys
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

SCRIPT_DIR = Path(__file__).resolve().parent


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    # Dataclasses resolve string annotations through sys.modules.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


VALIDATOR = load_module("validate_contribution", "validate-contribution.py")
PUBLISH = load_module("publish_open_pr_checks", "publish-open-pr-checks.py")

MAX_PRS = 100
MAX_RUN_ATTEMPTS = 5
MAX_ARTIFACT_BYTES = 20_000_000
MAX_REPORT_BYTES = 10_000_000
BOT_LOGIN = "github-actions[bot]"
PR_LIST_RE = re.compile(r"[0-9]+(?:[\s,]+[0-9]+)*")


class StripAuthOnRedirect(HTTPRedirectHandler):
    """Never forward the GitHub token to the artifact storage host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            redirected.remove_header("Authorization")
        return redirected


def parse_pr_numbers(raw: str) -> list[int]:
    text = raw.strip()
    if not PR_LIST_RE.fullmatch(text):
        raise ValueError("BACKFILL_PRS must be PR numbers separated by spaces or commas")
    numbers = list(dict.fromkeys(int(item) for item in re.split(r"[\s,]+", text)))
    if len(numbers) > MAX_PRS or any(number < 1 for number in numbers):
        raise ValueError(f"BACKFILL_PRS must list between 1 and {MAX_PRS} positive PR numbers")
    return numbers


def gate_comment(comments: list[dict[str, object]]) -> dict[str, object] | None:
    """Return the bot-authored gate comment; user comments quoting the marker are ignored."""

    for comment in comments:
        user = comment.get("user")
        body = comment.get("body")
        if (isinstance(user, dict) and user.get("login") == BOT_LOGIN
                and isinstance(body, str) and PUBLISH.COMMENT_MARKER in body
                and isinstance(comment.get("id"), int)):
            return comment
    return None


def legacy_run_url(repository: str, body: str) -> str | None:
    match = re.search(rf"https://github\.com/{re.escape(repository)}/actions/runs/[0-9]+(?:/attempts/[0-9]+)?", body)
    return match.group(0) if match else None


def readme_entries(repository: str, base_sha: str, head_sha: str) -> list[object]:
    """Diff README.md between the merge base and head, read from the base repository.

    Merged forks are often deleted, but GitHub keeps every PR head reachable in
    the base repository, so exact SHAs work where branch names no longer do.
    """

    compare = VALIDATOR.github_json(f"https://api.github.com/repos/{repository}/compare/{base_sha}...{head_sha}")
    merge_base = compare.get("merge_base_commit") if isinstance(compare, dict) else None
    if not isinstance(merge_base, dict) or not isinstance(merge_base.get("sha"), str):
        raise VALIDATOR.ValidationError("GitHub did not return a merge base for this pull request")
    base_readme = VALIDATOR.content_file(repository, "README.md", merge_base["sha"])
    head_readme = VALIDATOR.content_file(repository, "README.md", head_sha)
    diff = "".join(difflib.unified_diff(base_readme.splitlines(keepends=True), head_readme.splitlines(keepends=True),
                                        fromfile="README.md", tofile="README.md"))
    malformed = VALIDATOR.malformed_community_plugin_lines(diff, base_readme, head_readme)
    if malformed:
        raise VALIDATOR.ValidationError(
            "Community Plugins entries must use `- [Name](https://github.com/owner/repo) - description`: "
            + "; ".join(malformed)
        )
    return VALIDATOR.get_new_readme_entries_from_diff(diff, base_readme, head_readme)


def pull_request_result(repository: str, pull: dict[str, object]) -> dict[str, object]:
    """Rebuild the validator's per-PR result for a closed pull request."""

    head, base, user = pull.get("head"), pull.get("base"), pull.get("user")
    head_sha = head.get("sha") if isinstance(head, dict) else None
    base_sha = base.get("sha") if isinstance(base, dict) else None
    if not all(isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha) for sha in (head_sha, base_sha)):
        raise RuntimeError("GitHub returned a pull request without exact base/head SHAs")
    login = user.get("login") if isinstance(user, dict) else ""
    result: dict[str, object] = {
        "pr_number": pull["number"],
        "title": pull.get("title") if isinstance(pull.get("title"), str) else "",
        "head_sha": head_sha,
        "author_login": login if isinstance(login, str) else "",
        "pr_state": "merged" if pull.get("merged") is True else "closed",
        "state": "success",
        "contributions": [],
        "failure_reasons": [],
    }
    try:
        entries = readme_entries(repository, base_sha, head_sha)
    except VALIDATOR.ValidationError as error:
        return {**result, "state": "failure", "failure_reasons": [str(error)]}

    contributions: list[dict[str, str]] = []
    failures: list[str] = []
    for entry in entries:
        try:
            default_branch = VALIDATOR.ensure_public_github_repository(entry)
        except VALIDATOR.ValidationError as error:
            failures.append(str(error))
            continue
        contribution = {"owner": entry.owner, "repo": entry.repo,
                        "scanner_ci": VALIDATOR.inspect_scanner_ci(entry).status}
        if default_branch:
            contribution["default_branch"] = default_branch
        contributions.append(contribution)
    state = "failure" if failures else "scan" if contributions else "success"
    return {**result, "state": state, "contributions": contributions, "failure_reasons": failures}


def download_report(repository: str, token: str, artifact_id: int) -> object:
    request = Request(
        f"{PUBLISH.API_ROOT}/repos/{repository}/actions/artifacts/{artifact_id}/zip",
        headers={"Authorization": f"Bearer {token}", "User-Agent": PUBLISH.USER_AGENT},
    )
    try:
        with build_opener(StripAuthOnRedirect).open(request, timeout=PUBLISH.REQUEST_TIMEOUT_SECONDS) as response:
            archive = response.read(MAX_ARTIFACT_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError):
        return None
    if len(archive) > MAX_ARTIFACT_BYTES:
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            info = bundle.getinfo("findings.json")
            if info.file_size > MAX_REPORT_BYTES:
                return None
            return json.loads(bundle.read(info))
    except (KeyError, zipfile.BadZipFile, UnicodeError, json.JSONDecodeError):
        return None


def latest_report(repository: str, token: str, number: int, owner: str, repo: str) -> tuple[dict[str, object] | None, str | None]:
    """Return the newest retained run-bound report for one contribution and its run URL."""

    candidates: list[tuple[str, int, int, int]] = []
    for attempt in range(1, MAX_RUN_ATTEMPTS + 1):
        name = f"scan-findings-pr-{number}-{owner}-{repo}-attempt-{attempt}"
        payload = PUBLISH.github_api(repository, f"/actions/artifacts?{urlencode({'name': name, 'per_page': 100})}", token)
        artifacts = payload.get("artifacts") if isinstance(payload, dict) else None
        for artifact in artifacts if isinstance(artifacts, list) else []:
            run = artifact.get("workflow_run") if isinstance(artifact, dict) else None
            if (isinstance(run, dict) and isinstance(run.get("id"), int) and isinstance(artifact.get("id"), int)
                    and artifact.get("expired") is False and isinstance(artifact.get("created_at"), str)):
                candidates.append((artifact["created_at"], artifact["id"], run["id"], attempt))
    for _, artifact_id, run_id, attempt in sorted(candidates, reverse=True):
        report = download_report(repository, token, artifact_id)
        if PUBLISH.valid_report(report, str(run_id), attempt):
            return report, f"https://github.com/{repository}/actions/runs/{run_id}/attempts/{attempt}"
    return None, None


def backfill_pull_request(repository: str, token: str, number: int, fallback_run_url: str) -> str:
    """Re-render one legacy gate comment. Returns what happened, for the log."""

    comment = gate_comment(PUBLISH.list_issue_comments(repository, number, token))
    if comment is None:
        return "skipped: no gate comment"
    body = comment["body"]
    if body.lstrip().startswith("## "):
        return "skipped: already current"
    pull = PUBLISH.github_api(repository, f"/pulls/{number}", token)
    if not isinstance(pull, dict) or pull.get("number") != number:
        raise RuntimeError("GitHub returned an invalid pull request")
    if pull.get("state") != "closed":
        return "skipped: open pull requests are refreshed by the sweep"

    result = pull_request_result(repository, pull)
    run_url = legacy_run_url(repository, body) or fallback_run_url
    reports = None
    if result["state"] == "scan":
        reports = []
        for contribution in result["contributions"]:
            report, report_run_url = latest_report(repository, token, number, contribution["owner"], contribution["repo"])
            reports.append({"owner": contribution["owner"], "repo": contribution["repo"], "report": report})
            run_url = report_run_url or run_url
    # A merge required a passing gate, so a merged PR without a retained report still passed.
    conclusion = "success" if result["state"] == "success" or (
        result["state"] == "scan" and result["pr_state"] == "merged") else "failure"
    new_body = PUBLISH.remediation_comment(result, conclusion, "", "Contribution checks failed", run_url, reports)
    PUBLISH.github_api(repository, f"/issues/comments/{comment['id']}", token, method="PATCH", payload={"body": new_body})
    scored = sum(1 for item in reports or [] if item["report"] is not None)
    return f"updated ({result['pr_state']}, {result['state']}, {scored} scored report(s))"


def main() -> int:
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    run_url = os.environ.get("BACKFILL_RUN_URL", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or not token or not run_url:
        print("ERROR: GITHUB_REPOSITORY, GITHUB_TOKEN, and BACKFILL_RUN_URL are required", file=sys.stderr)
        return 1
    try:
        numbers = parse_pr_numbers(os.environ.get("BACKFILL_PRS", ""))
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    failures = 0
    for number in numbers:
        try:
            outcome = backfill_pull_request(repository, token, number, run_url)
        except (RuntimeError, VALIDATOR.ValidationError) as error:
            failures += 1
            outcome = f"FAILED: {error}"
        print(f"PR #{number}: {outcome}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
