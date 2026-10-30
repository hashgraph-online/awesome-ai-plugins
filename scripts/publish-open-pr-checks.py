#!/usr/bin/env python3
"""Publish the result of an open-PR sweep on each pull request head commit."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

API_ROOT = "https://api.github.com"
CHECK_NAME = "Open Plugin Contribution Gate"
COMMENT_MARKER = "<!-- awesome-ai-plugins-contribution-gate -->"
CONTRIBUTING_URL = "https://github.com/hashgraph-online/awesome-ai-plugins/blob/main/CONTRIBUTING.md"
SCANNER_GUIDE_URL = "https://github.com/hashgraph-online/awesome-ai-plugins/blob/main/SCANNER_GUIDE.md"
USER_AGENT = "awesome-ai-plugins-open-pr-sweep"
JOB_CONCLUSION_RE = re.compile(
    r"\b(success|failure|neutral|cancelled|skipped|timed_out|action_required|startup_failure)\b"
)
REQUEST_TIMEOUT_SECONDS = 30
SCAN_LOOKUP_ATTEMPTS = 6
SCAN_LOOKUP_RETRY_SECONDS = 5
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


def scan_conclusions(
    repository: str,
    run_id: str,
    token: str,
    expected: dict[int, int] | None = None,
    run_attempt: int = 1,
) -> dict[int, list[str]]:
    """Read the latest scanner attempts, waiting briefly for job data to appear."""

    expected = expected or {}
    for attempt in range(SCAN_LOOKUP_ATTEMPTS):
        latest: dict[str, tuple[tuple[int, int], int, str | None]] = {}
        page = 1
        while True:
            payload = github_api(
                repository, f"/actions/runs/{run_id}/jobs?per_page=100&filter=all&page={page}", token
            )
            if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
                raise RuntimeError("GitHub returned an invalid jobs response")
            jobs = payload["jobs"]
            for job in jobs:
                if not isinstance(job, dict) or not isinstance(job.get("name"), str):
                    continue
                name = job["name"]
                match = SCAN_JOB_RE.search(name)
                if not match:
                    continue
                number = int(match.group(1) or match.group(2))
                job_attempt = job.get("run_attempt")
                job_id = job.get("id")
                order = (
                    job_attempt if isinstance(job_attempt, int) else 1,
                    job_id if isinstance(job_id, int) else 0,
                )
                conclusion = job.get("conclusion")
                if name not in latest or order > latest[name][0]:
                    latest[name] = (order, number, conclusion if isinstance(conclusion, str) else None)
            if len(jobs) < 100:
                break
            page += 1

        conclusions: dict[int, list[str]] = {}
        pending: set[int] = set()
        for name, (order, number, conclusion) in latest.items():
            # The jobs list can lag behind a finished matrix job. Confirm only
            # the latest pending job directly, preserving run/attempt identity.
            if conclusion is None and order[0] >= run_attempt and order[1] > 0:
                detail = github_api(repository, f"/actions/jobs/{order[1]}", token)
                if (
                    isinstance(detail, dict)
                    and detail.get("id") == order[1]
                    and str(detail.get("run_id")) == run_id
                    and detail.get("run_attempt") == order[0]
                    and detail.get("name") == name
                    and detail.get("status") == "completed"
                    and isinstance(detail.get("conclusion"), str)
                ):
                    conclusion = detail["conclusion"]
            if conclusion is None or order[0] < run_attempt:
                pending.add(number)
            else:
                conclusions.setdefault(number, []).append(conclusion)
        incomplete = {
            number for number, count in expected.items()
            if len(conclusions.get(number, [])) < count or number in pending
        }
        if not incomplete:
            return conclusions
        if attempt + 1 < SCAN_LOOKUP_ATTEMPTS:
            time.sleep(SCAN_LOOKUP_RETRY_SECONDS)

    # A partial matrix must never be reported as passing.
    for number in incomplete:
        conclusions.pop(number, None)
    return conclusions


def artifact_conclusions(results: list, directory: str, run_id: str, run_attempt: int) -> dict[int, list[str]]:
    """Use only complete, run-bound sanitized reports from the trusted scan jobs."""
    root = Path(directory).resolve()
    conclusions: dict[int, list[str]] = {}
    for result in results:
        if result.get("state") != "scan":
            continue
        number = result["pr_number"]
        verdicts = []
        for contribution in result["contributions"]:
            if not isinstance(contribution, dict):
                break
            owner, repo = contribution.get("owner"), contribution.get("repo")
            if not all(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", value) for value in (owner, repo)):
                break
            report_file = (root / f"scan-findings-pr-{number}-{owner}-{repo}-attempt-{run_attempt}" / "findings.json").resolve()
            try:
                if not report_file.is_relative_to(root) or report_file.stat().st_size > 10_000_000:
                    break
                report = json.loads(report_file.read_text())
                if not isinstance(report, dict):
                    break
                score = report.get("score")
                if (report.get("schema_version") != "sanitized-scan.v1"
                        or report.get("workflow_run_id") != run_id
                        or type(report.get("workflow_run_attempt")) is not int
                        or report.get("workflow_run_attempt") != run_attempt
                        or not isinstance(report.get("source_commit"), str)
                        or not re.fullmatch(r"[0-9a-f]{40}", report["source_commit"])
                        or type(score) not in (int, float) or not 0 <= score <= 100):
                    break
            except (OSError, UnicodeError, json.JSONDecodeError):
                break
            verdicts.append("success" if score >= 75 else "failure")
        # A partial or stale matrix can never clear the PR.
        if len(verdicts) == len(result["contributions"]):
            conclusions[number] = verdicts
    return conclusions


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
        details = "; ".join(item for item in failure_lines if isinstance(item, str))
        return (
            "failure",
            "Contribution requirements failed",
            one_paragraph(f"Catalog validation failed: {details or 'review the reported catalog findings.'}"),
        )

    if state == "scan":
        jobs = scanner_jobs.get(number, [])
        if not jobs:
            return (
                "failure",
                "Centralized score unavailable",
                "The centralized scan did not establish a score of at least 75. Keep the contribution blocked and rerun the scan before merge.",
            )
        if all(conclusion == "success" for conclusion in jobs):
            return (
                "success",
                "Centralized score threshold passed",
                "The centralized scan met the score threshold of 75; finding severities are advisory.",
            )
        job_details = ", ".join(jobs)
        return (
            "failure",
            "Centralized scan did not pass",
            f"The centralized scan jobs ended with {job_details}. A non-successful job may still report a numeric score; "
            "the job conclusion alone cannot distinguish a below-75 score from a scanner execution failure. "
            "Check the original scan job log and sanitized findings for the measured score or execution error. "
            "Keep the contribution blocked until a completed catalog-owned scan verifies a score of at least 75. "
            "Finding severities are advisory for listing eligibility.",
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


def one_paragraph(text: str) -> str:
    """Collapse whitespace for a note that should stay a single paragraph."""

    return " ".join(text.split())


def prose_sentences(text: str) -> list[str]:
    """Split scan prose into sentences, including clauses joined by a semicolon."""

    collapsed = " ".join(text.split())
    sentences: list[str] = []
    for item in re.split(r"(?<=[.])\s+", collapsed):
        item = item.strip()
        if not item:
            continue
        pieces = [part.strip() for part in item.split("; ") if part.strip()]
        for index, piece in enumerate(pieces):
            piece = piece.replace("a below-75 score", "a score below 75")
            if index > 0 and piece[:1].islower():
                piece = piece[0].upper() + piece[1:]
            if piece[-1] not in ".!?":
                piece += "."
            sentences.append(piece)
    return sentences


def status_prose(summary: str) -> str:
    """Return the score explanation without the findings link appended for the check run."""

    head, _, _ = summary.partition("[Open the source scan job summaries")
    return head.strip()


def emphasize_job_conclusions(sentence: str) -> str:
    """Mark GitHub job conclusions in the opening line so they read as status values."""

    if "ended with " not in sentence:
        return sentence
    prefix, _, tail = sentence.partition("ended with ")
    return f"{prefix}ended with {JOB_CONCLUSION_RE.sub(r'`\1`', tail)}"


def findings_markdown(artifact_prefix: str, run_url: str) -> str:
    """Return the sanitized-report instructions contributors use to inspect a scan."""

    return "\n".join(
        [
            f"[Open the source scan job summaries and download sanitized JSON/SARIF findings]({run_url}).",
            "",
            f"Look for the `{artifact_prefix}` artifact.",
            "",
            "- Reports include rule IDs and file locations.",
            "- Reports omit source snippets and credential values.",
            "- A missing report indicates a scanner execution problem, not a passing scan.",
        ]
    )


def reference_lines(run_url: str) -> list[str]:
    """Return the standard links for a contribution-gate comment."""

    return [
        "### References",
        "",
        f"- [Contribution requirements]({CONTRIBUTING_URL})",
        f"- [Scanner guide]({SCANNER_GUIDE_URL})",
        f"- [Latest sweep]({run_url})",
    ]


def scan_block_comment(result: dict[str, object], summary: str, run_url: str, mention: str) -> str:
    """Keep scan remediation in one paragraph without treating job failure as a score."""

    sentences = prose_sentences(status_prose(summary))
    if not sentences:
        sentences = ["The centralized scan did not establish a score of at least 75."]
    lead = emphasize_job_conclusions(sentences[0])
    if lead[:1].isupper():
        lead = lead[0].lower() + lead[1:]
    number = result.get("pr_number")
    artifact_prefix = f"scan-findings-pr-{number}-" if isinstance(number, int) else "scan-findings-pr-"
    parts = [
        f"{COMMENT_MARKER} {mention}, {lead}",
        f"Check the [sanitized findings]({run_url}) in `{artifact_prefix}` for the measured score and rule locations; a failed job alone does not establish a score below 75.",
        "Fix or document rule-level findings, rerun the catalog scan, and request review once a catalog-owned scan verifies a score of at least 75; finding severities are advisory.",
        "Source-repository scanner CI is optional.",
    ]
    if missing_scanner_ci_repos(result):
        parts.append(
            f"If the completed report confirms a score below 75, source-repository scanner CI is recommended, not required: use the SHA-pinned workflow in the [scanner guide]({SCANNER_GUIDE_URL}). "
            "Reproduce locally with the appropriate ecosystem, run the same pinned scanner in CI, and link the source run for re-review."
        )
    return one_paragraph(" ".join(parts))


def remediation_comment(
    result: dict[str, object],
    conclusion: str,
    check_title: str,
    summary: str,
    run_url: str,
) -> str:
    """Build an idempotent contributor-facing contribution-gate comment."""

    author_login = result.get("author_login")
    mention = f"@{author_login}" if isinstance(author_login, str) and GITHUB_LOGIN_RE.fullmatch(author_login) else "the contributor"

    if conclusion == "success":
        if result.get("state") == "scan":
            message = "The centralized score threshold of 75 was met; finding severities are advisory."
        else:
            message = "No new Community Plugins entries require validation in this pull request."
        return one_paragraph(
            f"{COMMENT_MARKER} {mention}, contribution checks passed. {message} No action is needed. [Latest sweep]({run_url})."
        )

    if result.get("state") == "scan":
        return scan_block_comment(result, summary, run_url, mention)

    guidance = "Use the Community Plugins format `- [Name](https://github.com/owner/repo) - description`, put the entry in the correct section, keep it alphabetical, and avoid duplicates."
    return one_paragraph(
        f"{COMMENT_MARKER} {mention}, {summary} {guidance} "
        f"See [contribution requirements]({CONTRIBUTING_URL}) and "
        f"[scanner guide]({SCANNER_GUIDE_URL}). [Latest sweep]({run_url})."
    )


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
    if conclusion == "success" and existing is None:
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
    rendered = summary
    if result.get("state") == "scan":
        rendered = "\n\n".join(prose_sentences(summary))
        rendered += f"\n\n{findings_markdown(f'scan-findings-pr-{number}-', run_url)}"
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
            "summary": f"PR #{number} — {title}\n\n{rendered}",
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
    attempt_text = os.environ.get("GITHUB_RUN_ATTEMPT", "1").strip()
    run_url = os.environ.get("SWEEP_RUN_URL", "").strip()
    results_json = os.environ.get("OPEN_PR_RESULTS", "[]")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        print("ERROR: GITHUB_REPOSITORY must be an owner/repository pair", file=sys.stderr)
        return 1
    if not token or not run_id or not run_url:
        print("ERROR: GITHUB_TOKEN, GITHUB_RUN_ID, and SWEEP_RUN_URL are required", file=sys.stderr)
        return 1
    if not attempt_text.isdigit() or int(attempt_text) < 1:
        print("ERROR: GITHUB_RUN_ATTEMPT must be a positive integer", file=sys.stderr)
        return 1
    try:
        results = json.loads(results_json)
        if not isinstance(results, list):
            raise RuntimeError("OPEN_PR_RESULTS must be a JSON array")
        expected: dict[int, int] = {}
        for result in results:
            if not isinstance(result, dict):
                raise RuntimeError("validator returned a non-object PR result")
            if result.get("state") == "scan":
                number, contributions = result.get("pr_number"), result.get("contributions")
                if not isinstance(number, int) or not isinstance(contributions, list) or not contributions:
                    raise RuntimeError("scanner result is missing its PR number or contributions")
                expected[number] = len(contributions)
        findings_dir = os.environ.get("SCAN_FINDINGS_DIR", "").strip()
        scanner_jobs = (artifact_conclusions(results, findings_dir, run_id, int(attempt_text))
                        if findings_dir else scan_conclusions(repository, run_id, token, expected, int(attempt_text)))
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
