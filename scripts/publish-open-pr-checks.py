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
MIN_SCORE = 75
REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_TEMPLATE = REPO_ROOT / "templates/hol-plugin-scanner.yml"
WORKFLOW_TEMPLATE_URL = "https://github.com/hashgraph-online/awesome-ai-plugins/blob/main/templates/hol-plugin-scanner.yml"
WORKFLOW_PATH = ".github/workflows/hol-plugin-scanner.yml"
NAME_RE = re.compile(r"[A-Za-z0-9_.-]+")
BRANCH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}")
RULE_RE = re.compile(r"[A-Z0-9_.-]{1,100}")
PATH_RE = re.compile(r"[A-Za-z0-9_./+ -]{1,240}")
LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ()/&.,:'+-]{0,79}")
VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
REMEDIATION_RE = re.compile(r"[A-Za-z0-9 .,;:()'/-]{1,300}")
GENERIC_REMEDIATION = "Review this rule at the reported location and fix it, or explain a false positive for maintainer review."
SEVERITY_ORDER = ("critical", "high", "medium", "low", "info")
SEVERITY_BADGES = {"critical": "🟣 critical", "high": "🔴 high", "medium": "🟠 medium", "low": "🟡 low", "info": "⚪ info"}
MAX_RULE_ROWS = 25
MAX_LOCATION_ROWS = 50


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


def valid_report(report: object, run_id: str, run_attempt: int) -> bool:
    """Accept only a sanitized report bound to this exact workflow attempt."""

    if not isinstance(report, dict):
        return False
    score = report.get("score")
    return (report.get("schema_version") == "sanitized-scan.v1"
            and report.get("workflow_run_id") == run_id
            and type(report.get("workflow_run_attempt")) is int
            and report.get("workflow_run_attempt") == run_attempt
            and isinstance(report.get("source_commit"), str)
            and re.fullmatch(r"[0-9a-f]{40}", report["source_commit"]) is not None
            and type(score) in (int, float) and 0 <= score <= 100)


def load_scan_reports(results: list, directory: str, run_id: str, run_attempt: int) -> dict[int, list[dict[str, object]]]:
    """Load the run-bound sanitized report for each scanned contribution.

    A contribution without a valid report maps to ``None`` so callers can show
    which source failed to produce a score.
    """
    root = Path(directory).resolve()
    reports: dict[int, list[dict[str, object]]] = {}
    for result in results:
        if result.get("state") != "scan":
            continue
        number = result["pr_number"]
        loaded: list[dict[str, object]] = []
        for contribution in result["contributions"]:
            if not isinstance(contribution, dict):
                break
            owner, repo = contribution.get("owner"), contribution.get("repo")
            if not all(isinstance(value, str) and NAME_RE.fullmatch(value) for value in (owner, repo)):
                break
            report_file = (root / f"scan-findings-pr-{number}-{owner}-{repo}-attempt-{run_attempt}" / "findings.json").resolve()
            report = None
            try:
                if report_file.is_relative_to(root) and report_file.stat().st_size <= 10_000_000:
                    report = json.loads(report_file.read_text())
            except (OSError, UnicodeError, json.JSONDecodeError):
                report = None
            loaded.append({"owner": owner, "repo": repo,
                           "report": report if valid_report(report, run_id, run_attempt) else None})
        reports[number] = loaded
    return reports


def report_verdicts(results: list, reports: dict[int, list[dict[str, object]]]) -> dict[int, list[str]]:
    """Map loaded reports to per-contribution verdicts; any gap keeps the PR blocked."""

    conclusions: dict[int, list[str]] = {}
    for result in results:
        if result.get("state") != "scan":
            continue
        number = result["pr_number"]
        loaded = reports.get(number, [])
        # A partial or stale matrix can never clear the PR.
        if len(loaded) != len(result["contributions"]) or any(item["report"] is None for item in loaded):
            continue
        conclusions[number] = ["success" if item["report"]["score"] >= MIN_SCORE else "failure" for item in loaded]
    return conclusions


def artifact_conclusions(results: list, directory: str, run_id: str, run_attempt: int) -> dict[int, list[str]]:
    """Use only complete, run-bound sanitized reports from the trusted scan jobs."""
    return report_verdicts(results, load_scan_reports(results, directory, run_id, run_attempt))


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


def format_score(value: object) -> str:
    """Render a score without trailing float noise."""

    if isinstance(value, float) and not value.is_integer():
        return f"{value:.1f}"
    return str(int(value)) if isinstance(value, (int, float)) else "?"


def repo_link(owner: str, repo: str) -> str:
    return f"[{owner}/{repo}](https://github.com/{owner}/{repo})"


def commit_link(owner: str, repo: str, commit: str) -> str:
    return f"[`{commit[:7]}`](https://github.com/{owner}/{repo}/tree/{commit})"


def report_findings(report: dict[str, object]) -> list[dict[str, object]]:
    """Re-check sanitized findings before they reach Markdown."""

    findings = []
    items = report.get("findings")
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        rule, severity = item.get("ruleId"), item.get("severity")
        if not isinstance(rule, str) or not RULE_RE.fullmatch(rule) or severity not in SEVERITY_ORDER:
            continue
        path, line = item.get("filePath"), item.get("lineNumber")
        remediation = item.get("remediation")
        if not isinstance(remediation, str) or not REMEDIATION_RE.fullmatch(remediation):
            remediation = GENERIC_REMEDIATION
        finding: dict[str, object] = {"ruleId": rule, "severity": severity, "remediation": remediation}
        if isinstance(path, str) and PATH_RE.fullmatch(path) and ".." not in path.split("/") and not path.startswith("/"):
            finding["filePath"] = path
            if type(line) is int and line > 0:
                finding["lineNumber"] = line
        findings.append(finding)
    return findings


def table_cell(text: str) -> str:
    """Keep validator text inside one table cell or list item."""

    return " ".join(text.replace("|", "/").split())


def addressed(mention: str | None, text: str) -> str:
    """Prefix a sentence with the contributor mention when one is available."""

    return f"{mention}, {text[0].lower()}{text[1:]}" if mention else text


def rule_rows(findings: list[dict[str, object]]) -> list[str]:
    """Group findings by rule so contributors see one fix per problem."""

    grouped: dict[str, dict[str, object]] = {}
    for finding in findings:
        entry = grouped.setdefault(finding["ruleId"], {"severity": finding["severity"], "count": 0,
                                                       "remediation": finding["remediation"]})
        entry["count"] += 1
        if SEVERITY_ORDER.index(finding["severity"]) < SEVERITY_ORDER.index(entry["severity"]):
            entry["severity"] = finding["severity"]
    ordered = sorted(grouped.items(), key=lambda item: (SEVERITY_ORDER.index(item[1]["severity"]), -item[1]["count"], item[0]))
    rows = ["| Severity | Rule | Hits | How to fix |", "| --- | --- | ---: | --- |"]
    for rule, entry in ordered[:MAX_RULE_ROWS]:
        rows.append(f"| {SEVERITY_BADGES[entry['severity']]} | `{rule}` | {entry['count']} | {table_cell(entry['remediation'])} |")
    if len(ordered) > MAX_RULE_ROWS:
        rows.append(f"| | {len(ordered) - MAX_RULE_ROWS} more rules | | See the full report artifact. |")
    return rows


def location_rows(owner: str, repo: str, commit: str, findings: list[dict[str, object]]) -> list[str]:
    """Link each distinct finding location to the exact scanned line."""

    counts: dict[tuple[str, str, str, int], int] = {}
    for finding in findings:
        key = (finding["severity"], finding["ruleId"], finding.get("filePath", ""), finding.get("lineNumber", 0))
        counts[key] = counts.get(key, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (SEVERITY_ORDER.index(item[0][0]), *item[0][1:]))
    rows = ["| Severity | Rule | Location |", "| --- | --- | --- |"]
    for (severity, rule, path, line), count in ordered[:MAX_LOCATION_ROWS]:
        if not path:
            location = "Repository-level"
        elif rule.endswith("_MISSING") and not line:
            # The scanner names the file it expected; there is nothing to link to yet.
            location = f"`{path}` (add this file)"
        else:
            label = f"{path}:{line}" if line else path
            anchor = f"#L{line}" if line else ""
            location = f"[`{label}`](https://github.com/{owner}/{repo}/blob/{commit}/{quote(path, safe='/')}{anchor})"
        suffix = f" ×{count}" if count > 1 else ""
        rows.append(f"| {severity} | `{rule}` | {location}{suffix} |")
    if len(ordered) > MAX_LOCATION_ROWS:
        rows.append(f"| | | {len(ordered) - MAX_LOCATION_ROWS} more in the full report artifact |")
    return rows


def category_rows(report: dict[str, object]) -> list[str]:
    """Show where points were lost, using scanner labels that passed the allowlist."""

    rows: list[str] = []
    items = report.get("categories")
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not LABEL_RE.fullmatch(item["name"]):
            continue
        score, maximum = item.get("score"), item.get("max")
        if type(score) not in (int, float) or type(maximum) not in (int, float) or maximum <= 0 or not 0 <= score <= maximum:
            continue
        lost = []
        checks = item.get("failed_checks")
        for check in checks if isinstance(checks, list) else []:
            if (isinstance(check, dict) and isinstance(check.get("name"), str) and LABEL_RE.fullmatch(check["name"])
                    and type(check.get("points")) in (int, float) and type(check.get("max")) in (int, float)
                    and check["max"] > check["points"]):
                lost.append(f"{check['name']} (−{format_score(check['max'] - check['points'])})")
        status = "✅" if score == maximum else ("⚠️" if score / maximum >= 0.5 else "❌")
        rows.append(f"| {status} {item['name']} | {format_score(score)} / {format_score(maximum)} | {'; '.join(lost) or '—'} |")
    if not rows:
        return []
    return ["| Category | Points | Lost on |", "| --- | ---: | --- |", *rows]


def summary_row(item: dict[str, object]) -> str:
    owner, repo, report = item["owner"], item["repo"], item["report"]
    if report is None:
        return f"| {repo_link(owner, repo)} | — | — | {MIN_SCORE} | ⚠️ No score produced |"
    score = report["score"]
    status = "✅ Passes" if score >= MIN_SCORE else f"❌ {format_score(MIN_SCORE - score)} points short"
    return (f"| {repo_link(owner, repo)} | {commit_link(owner, repo, report['source_commit'])} | "
            f"**{format_score(score)}** | {MIN_SCORE} | {status} |")


def summary_table(items: list[dict[str, object]]) -> list[str]:
    return ["| Source repository | Scanned commit | Score | Needed | Result |",
            "| --- | --- | ---: | ---: | --- |",
            *(summary_row(item) for item in items)]


def workflow_template() -> str | None:
    try:
        text = WORKFLOW_TEMPLATE.read_text(encoding="utf-8")
    except OSError:
        return None
    return text if "hashgraph-online/ai-plugin-scanner-action@" in text else None


def install_link(owner: str, repo: str, branch: object, template: str) -> str | None:
    """Open GitHub's new-file editor with the pinned workflow pre-filled.

    Nothing is committed until the maintainer reviews the file and commits it,
    and contributors without write access get GitHub's fork-and-propose flow.
    """
    if not isinstance(branch, str) or not BRANCH_RE.fullmatch(branch) or ".." in branch:
        return None
    return (f"https://github.com/{owner}/{repo}/new/{quote(branch, safe='/')}"
            f"?filename={quote(WORKFLOW_PATH, safe='/')}&value={quote(template, safe='')}")


def scanner_ci_section(result: dict[str, object], items: list[dict[str, object]], passed: bool) -> list[str]:
    """Recommend maintainer-owned scanner CI with a reviewed one-click install."""

    if not items:
        return []
    contributions = {(c.get("owner"), c.get("repo")): c for c in result.get("contributions", []) if isinstance(c, dict)}
    missing = [item for item in items if contributions.get((item["owner"], item["repo"]), {}).get("scanner_ci") != "maintained"]
    if not missing:
        return ["### 🛡️ Scanner CI", "", "✅ Scanner CI detected in your repository. Thanks for keeping it continuously checked."]
    template = workflow_template()
    version = next((item["report"].get("scanner_version") for item in items
                    if item["report"] and isinstance(item["report"].get("scanner_version"), str)
                    and VERSION_RE.fullmatch(item["report"]["scanner_version"])), None)
    lines = [
        "### 🛡️ Add the scanner to your repository (recommended)",
        "",
        "Run the same pinned scanner on every push so you see score changes in your own pull requests before reviewers do."
        if not passed else "Keep this score from slipping: run the same pinned scanner on every push in your own repository.",
        "",
        "- 📈 **Full Registry trust score.** Listings without maintainer scanner CI get a 10% trust-score reduction.",
        "- ⚡ **Faster re-review.** Fixes are verified in your CI before the catalog re-scans.",
        "- 🔒 **Locked down.** `contents: read` only, no secrets, credentials not persisted, every action pinned to a commit SHA.",
        "",
    ]
    links = []
    for item in missing:
        contribution = contributions.get((item["owner"], item["repo"]), {})
        link = install_link(item["owner"], item["repo"], contribution.get("default_branch"), template) if template else None
        if link:
            links.append(f"**[➕ Add scanner workflow to {item['owner']}/{item['repo']}]({link})**")
    if links:
        lines += [
            *links,
            "",
            f"Opens GitHub's editor with `{WORKFLOW_PATH}` pre-filled. Nothing changes until you review the file and commit it; "
            "without write access, GitHub offers to propose it from a fork.",
            "",
        ]
    lines += ["<details>", "<summary>Copy the workflow or run the scanner locally</summary>", ""]
    if template:
        lines += [f"Save as `{WORKFLOW_PATH}` ([template]({WORKFLOW_TEMPLATE_URL})):", "", "```yaml", template.rstrip(), "```", ""]
    spec = f"plugin-scanner=={version}" if version else "plugin-scanner"
    lines += [
        "Reproduce the catalog score locally" + (f" with the same scanner version ({version}):" if version else ":"),
        "",
        "```bash",
        f"pipx run --spec {spec} plugin-scanner scan . --min-score {MIN_SCORE}",
        "```",
        "",
        "</details>",
        "",
        "<sub>Scanner CI is optional for listing. The catalog's own scan is what gates this pull request.</sub>",
    ]
    return lines


def rescan_section() -> list[str]:
    return [
        "### 🔁 Re-scan after fixing",
        "",
        "The catalog scans the latest commit on the source repository's default branch. Push your fixes there, then push any commit to this pull request to trigger a fresh scan:",
        "",
        "```bash",
        'git commit --allow-empty -m "chore: rescan plugin" && git push',
        "```",
        "",
        "Open pull requests are also re-scanned daily. This comment updates in place with the new score.",
    ]


def footer_lines(run_url: str, artifact_prefix: str) -> list[str]:
    return [
        "---",
        "",
        f"<sub>Severity labels are advisory; the score gates the listing. Reports omit source snippets and secret values. "
        f"[Sweep run and full JSON/SARIF report]({run_url}) (artifact `{artifact_prefix}`) · "
        f"[Scanner guide]({SCANNER_GUIDE_URL}) · [Contribution requirements]({CONTRIBUTING_URL})</sub>",
        "",
        COMMENT_MARKER,
    ]


def scan_report_comment(
    result: dict[str, object],
    items: list[dict[str, object]],
    run_url: str,
    mention: str | None,
    job_conclusions: list[str],
    conclusion: str,
) -> str:
    """Render the scan verdict as tables a contributor can act on."""

    number = result.get("pr_number")
    artifact_prefix = f"scan-findings-pr-{number}-" if isinstance(number, int) else "scan-findings-pr-"
    scored = [item for item in items if item["report"] is not None]
    unscored = [item for item in items if item["report"] is None]
    if scored:
        passed = not unscored and all(item["report"]["score"] >= MIN_SCORE for item in scored)
    else:
        passed = conclusion == "success"
    below = [item for item in scored if item["report"]["score"] < MIN_SCORE]
    pr_state = result.get("pr_state")

    if passed:
        title = "## ✅ Plugin scan passed"
        next_step = ("This listing has been merged; no action is needed." if pr_state == "merged"
                     else "No scan changes are needed; a maintainer will review the listing.")
        intro = addressed(mention, f"The catalog scan cleared the {MIN_SCORE}-point listing threshold. {next_step}")
    elif below:
        lowest = min(item["report"]["score"] for item in below)
        title = f"## ❌ Plugin scan: {format_score(lowest)} / 100 (needs {MIN_SCORE})"
        intro = addressed(mention, f"Thanks for the submission! The catalog scanned your plugin source and it needs "
                          f"**{format_score(MIN_SCORE - lowest)} more points** before this listing can merge. "
                          "The fixes below are ordered by severity.")
    else:
        conclusions = ", ".join(f"`{c}`" for c in job_conclusions if JOB_CONCLUSION_RE.fullmatch(c))
        ended = f"The scan job ended with {conclusions} but" if conclusions else "The scan"
        title = "## ⚠️ Plugin scan did not produce a score"
        intro = addressed(mention, f"{ended} did not produce a usable report, so no score was measured. "
                 f"A failed job alone does not mean your plugin scored below {MIN_SCORE}. "
                 + ("Check that the repository is public and clonable before resubmitting."
                    if pr_state in ("merged", "closed") else
                    "Check that the repository is public and clonable, then push to this pull request to retry. "
                    "If it keeps failing, a maintainer will investigate the scan job log."))

    lines = [title, "", intro]
    if items and (scored or not passed):
        lines += ["", *summary_table(items)]
    for item in scored:
        report = item["report"]
        owner, repo, commit = item["owner"], item["repo"], report["source_commit"]
        findings = report_findings(report)
        categories = category_rows(report)
        heading = f"{owner}/{repo}" if len(items) > 1 else "your repository"
        if not passed and findings:
            lines += ["", f"### 🔧 What to fix in {heading}", "", *rule_rows(findings)]
        if findings:
            noun = "finding" if len(findings) == 1 else "findings"
            lines += ["", "<details>", f"<summary>📍 {len(findings)} {noun} by location in {owner}/{repo}</summary>", "",
                      *location_rows(owner, repo, commit, findings), "", "</details>"]
        if categories:
            lines += ["", "<details>", f"<summary>📊 Score breakdown for {owner}/{repo}</summary>", "",
                      *categories, "", "</details>"]
    if any("SECRET" in f["ruleId"] or "CREDENTIAL" in f["ruleId"] for item in scored for f in report_findings(item["report"])):
        lines += ["", "> [!WARNING]",
                  "> Possible credentials were detected. If any are real, rotate them first; removing them from the latest commit does not remove them from Git history."]
    if not passed and pr_state in ("merged", "closed"):
        lines += ["", "> [!NOTE]", "> This pull request is closed. To resubmit, fix the findings and open a new pull request."]
    elif not passed:
        lines += ["", *rescan_section()]
    scanner_ci = scanner_ci_section(result, items, passed)
    if scanner_ci:
        lines += ["", *scanner_ci]
    lines += ["", *footer_lines(run_url, artifact_prefix)]
    return "\n".join(lines)


def remediation_comment(
    result: dict[str, object],
    conclusion: str,
    check_title: str,
    summary: str,
    run_url: str,
    reports: list[dict[str, object]] | None = None,
    job_conclusions: list[str] | None = None,
) -> str:
    """Build an idempotent contributor-facing contribution-gate comment."""

    author_login = result.get("author_login")
    mention = f"@{author_login}" if isinstance(author_login, str) and GITHUB_LOGIN_RE.fullmatch(author_login) else None

    if result.get("state") == "scan":
        items = reports if reports is not None else [
            {"owner": c["owner"], "repo": c["repo"], "report": None}
            for c in result.get("contributions", [])
            if isinstance(c, dict) and all(isinstance(c.get(k), str) and NAME_RE.fullmatch(c[k]) for k in ("owner", "repo"))
        ]
        return scan_report_comment(result, items, run_url, mention, job_conclusions or [], conclusion)

    if conclusion == "success":
        return "\n".join([
            "## ✅ Contribution checks passed",
            "",
            addressed(mention, "No new Community Plugins entries require validation in this pull request. No action is needed."),
            "",
            *footer_lines(run_url, "scan-findings-pr-"),
        ])

    reasons = result.get("failure_reasons")
    reason_lines = [f"- {table_cell(item)}" for item in reasons if isinstance(item, str)] if isinstance(reasons, list) else []
    return "\n".join([
        "## ❌ Contribution checks failed",
        "",
        addressed(mention, "The catalog entry needs a fix before it can be scanned:"),
        "",
        *(reason_lines or [f"- {table_cell(summary)}"]),
        "",
        "### ✍️ Expected format",
        "",
        "```markdown",
        "- [Name](https://github.com/owner/repo) - description",
        "```",
        "",
        "Put the entry in the correct section, keep it alphabetical, and avoid duplicates.",
        "",
        *footer_lines(run_url, "scan-findings-pr-"),
    ])


def upsert_remediation_comment(
    repository: str,
    result: dict[str, object],
    conclusion: str,
    check_title: str,
    summary: str,
    run_url: str,
    token: str,
    reports: list[dict[str, object]] | None = None,
    job_conclusions: list[str] | None = None,
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

    body = remediation_comment(result, conclusion, check_title, summary, run_url, reports, job_conclusions)
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
    reports: dict[int, list[dict[str, object]]] | None = None,
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
    pr_reports = (reports or {}).get(number)
    rendered = summary
    if result.get("state") == "scan" and pr_reports:
        scores = [item["report"]["score"] for item in pr_reports if item["report"] is not None]
        if scores and len(scores) == len(pr_reports):
            check_title = f"Scan score {format_score(min(scores))} / 100 (needs {MIN_SCORE})"
        body = scan_report_comment(result, pr_reports, run_url, None, scanner_jobs.get(number, []), conclusion)
        rendered = body.replace(COMMENT_MARKER, "").rstrip()
    elif result.get("state") == "scan":
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
    upsert_remediation_comment(repository, result, conclusion, check_title, summary, run_url, token,
                               pr_reports, scanner_jobs.get(number, []))


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
        reports: dict[int, list[dict[str, object]]] = {}
        if findings_dir:
            reports = load_scan_reports(results, findings_dir, run_id, int(attempt_text))
            scanner_jobs = report_verdicts(results, reports)
        else:
            scanner_jobs = scan_conclusions(repository, run_id, token, expected, int(attempt_text))
        for result in results:
            if not isinstance(result, dict):
                raise RuntimeError("validator returned a non-object PR result")
            publish_check(repository, result, scanner_jobs, run_url, token, reports)
    except (RuntimeError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
