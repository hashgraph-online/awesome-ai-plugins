#!/usr/bin/env python3
"""Validate new Awesome AI Plugins README contributions.

The catalog stores links to source repositories rather than plugin code.  This
validator therefore checks the contribution at the same boundary a maintainer
reviews it:

* only newly added Community Plugins entries are considered;
* each source repository is public and exposes workflow-based scanner CI; and
* the workflow is triggered by a push or pull request and invokes the HOL AI
  Plugin Scanner action.

The workflow that calls this script emits a matrix of source repositories.  A
follow-up job scans each repository with the same 80-point/high-severity gate
documented in CONTRIBUTING.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parent.parent
README_PATH = REPO_ROOT / "README.md"
REQUEST_TIMEOUT_SECONDS = 30
MAX_WORKFLOW_BYTES = 512 * 1024
USER_AGENT = "awesome-ai-plugins-contribution-validator"

README_ENTRY_RE = re.compile(
    r"^- \[([^\]]+)\]\((https://github\.com/"
    r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:[?#][^)]*)?)\)\s*[-\u2013\u2014]\s*(.+)$"
)
SCANNER_ACTION_RE = re.compile(
    r"^\s*(?:-\s*)?uses:\s*hashgraph-online/ai-plugin-scanner-action@[^\s#]+",
    re.IGNORECASE | re.MULTILINE,
)
TRIGGER_BLOCK_RE = re.compile(
    r"^\s*(?:pull_request|push)\s*:",
    re.IGNORECASE | re.MULTILINE,
)
TRIGGER_INLINE_RE = re.compile(
    r"^\s*on\s*:\s*(?:\[[^\]]*\b(?:pull_request|push)\b[^\]]*\]|(?:pull_request|push)\b)",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass(frozen=True)
class Contribution:
    display_name: str
    url: str
    owner: str
    repo: str
    description: str


class ValidationError(Exception):
    """A user-facing contribution validation error."""


def has_ci_trigger(workflow_text: str) -> bool:
    """Return whether a workflow runs on push or pull_request."""

    return bool(TRIGGER_BLOCK_RE.search(workflow_text) or TRIGGER_INLINE_RE.search(workflow_text))


def git(*args: str) -> str:
    """Run a read-only git command in the repository and return stdout."""

    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def normalize_url(url: str) -> str:
    """Normalize a GitHub repository URL for change-set comparison."""

    return url.rstrip("/").removesuffix(".git").lower()


def current_readme_section(readme_lines: list[str], line_number: int) -> str:
    """Return the nearest level-two heading before a 1-based line number."""

    heading = ""
    for index, line in enumerate(readme_lines, start=1):
        if index > line_number:
            break
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if match:
            heading = match.group(1).strip()
    return heading


def get_new_readme_entries(base_ref: str) -> list[Contribution]:
    """Find newly added Community Plugins entries in the README diff."""

    diff = git("diff", base_ref, "--", "README.md")
    if not diff or not README_PATH.exists():
        return []

    base_readme = git("show", f"{base_ref}:README.md")
    base_urls = {
        normalize_url(match.group(2))
        for match in README_ENTRY_RE.finditer(base_readme)
    }
    readme_lines = README_PATH.read_text(encoding="utf-8").splitlines()

    entries: list[Contribution] = []
    seen_urls: set[str] = set()
    added_line_number = 0
    for line in diff.splitlines():
        if line.startswith("@@"):
            hunk = re.search(r"\+(\d+)", line)
            added_line_number = int(hunk.group(1)) if hunk else 0
            continue
        if line.startswith("+") and not line.startswith("+++"):
            content = line[1:]
            match = README_ENTRY_RE.match(content.strip())
            if match and current_readme_section(readme_lines, added_line_number) == "Community Plugins":
                url = normalize_url(match.group(2))
                if url not in base_urls and url not in seen_urls:
                    seen_urls.add(url)
                    entries.append(
                        Contribution(
                            display_name=match.group(1).strip(),
                            url=match.group(2).strip(),
                            owner=match.group(3),
                            repo=match.group(4),
                            description=match.group(5).strip(),
                        )
                    )
            added_line_number += 1
            continue
        if not line.startswith("-"):
            added_line_number += 1

    return entries


def request_bytes(url: str) -> bytes:
    """Fetch a bounded GitHub API/raw response."""

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
    }
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_WORKFLOW_BYTES:
                raise ValidationError(f"workflow file is larger than {MAX_WORKFLOW_BYTES} bytes")
            payload = response.read(MAX_WORKFLOW_BYTES + 1)
    except HTTPError as error:
        if error.code == 404:
            raise ValidationError("source repository or workflow directory was not found") from error
        raise ValidationError(f"GitHub returned HTTP {error.code}") from error
    except (URLError, TimeoutError, OSError) as error:
        raise ValidationError(f"could not fetch GitHub metadata: {error}") from error

    if len(payload) > MAX_WORKFLOW_BYTES:
        raise ValidationError(f"workflow file is larger than {MAX_WORKFLOW_BYTES} bytes")
    return payload


def workflow_files(owner: str, repo: str) -> list[tuple[str, str]]:
    """Return (filename, text) pairs for a source repository's workflows."""

    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/.github/workflows"
    payload = json.loads(request_bytes(api_url).decode("utf-8"))
    if not isinstance(payload, list):
        raise ValidationError(".github/workflows is not a directory")

    files: list[tuple[str, str]] = []
    for item in payload:
        if not isinstance(item, dict) or item.get("type") != "file":
            continue
        name = str(item.get("name", ""))
        if not name.lower().endswith((".yml", ".yaml")):
            continue
        download_url = item.get("download_url")
        if not isinstance(download_url, str) or not download_url:
            continue
        text = request_bytes(download_url).decode("utf-8", errors="replace")
        files.append((name, text))
    return files


def validate_scanner_ci(contribution: Contribution) -> None:
    """Require a push/PR workflow that invokes the HOL scanner action."""

    try:
        files = workflow_files(contribution.owner, contribution.repo)
    except (ValidationError, json.JSONDecodeError) as error:
        raise ValidationError(
            f"{contribution.url} does not expose readable GitHub Actions workflows: {error}"
        ) from error

    scanner_workflows = [
        (name, text)
        for name, text in files
        if SCANNER_ACTION_RE.search(text)
    ]
    if not scanner_workflows:
        raise ValidationError(
            f"{contribution.url} must invoke "
            "hashgraph-online/ai-plugin-scanner-action in .github/workflows"
        )

    if not any(has_ci_trigger(text) for _, text in scanner_workflows):
        names = ", ".join(name for name, _ in scanner_workflows)
        raise ValidationError(
            f"{contribution.url} scanner workflow ({names}) must run on push or pull_request"
        )


def write_matrix(path: Path, entries: list[Contribution]) -> None:
    """Write the scanner job matrix as compact JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    matrix = [
        {"owner": entry.owner, "repo": entry.repo}
        for entry in entries
    ]
    path.write_text(json.dumps(matrix, separators=(",", ":")), encoding="utf-8")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-ref",
        default=os.environ.get("GITHUB_BASE_REF", "origin/main"),
        help="Git ref to compare against (default: GITHUB_BASE_REF or origin/main)",
    )
    parser.add_argument(
        "--matrix-output",
        type=Path,
        help="Write the scanner job matrix JSON to this path",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not git("rev-parse", "--verify", args.base_ref):
        print(f"ERROR: base ref '{args.base_ref}' is not available", file=sys.stderr)
        return 1

    entries = get_new_readme_entries(args.base_ref)
    if not entries:
        print("No new Community Plugins entries found; contribution checks are complete.")
        if args.matrix_output:
            write_matrix(args.matrix_output, [])
        return 0

    failures = 0
    for entry in entries:
        print(f"Checking {entry.display_name} ({entry.owner}/{entry.repo})...")
        try:
            validate_scanner_ci(entry)
        except ValidationError as error:
            failures += 1
            print(f"  FAIL: {error}", file=sys.stderr)
        else:
            print("  PASS: scanner CI is present and push/PR-triggered")

    if failures:
        print(f"\nContribution validation failed for {failures} entr{'y' if failures == 1 else 'ies'}.", file=sys.stderr)
        return 1

    print(f"\nAll {len(entries)} contribution entr{'y' if len(entries) == 1 else 'ies'} passed.")
    if args.matrix_output:
        write_matrix(args.matrix_output, entries)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
