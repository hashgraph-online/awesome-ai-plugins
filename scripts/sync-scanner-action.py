#!/usr/bin/env python3
"""Pin documented AI Plugin Scanner Action uses to the latest release commit."""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
ACTION_REPOSITORY = "hashgraph-online/ai-plugin-scanner-action"
API_ROOT = f"https://api.github.com/repos/{ACTION_REPOSITORY}"
USER_AGENT = "awesome-ai-plugins-action-sync"
REQUEST_TIMEOUT_SECONDS = 30
PINNED_FILES = (
    REPO_ROOT / "CONTRIBUTING.md",
    REPO_ROOT / "SCANNER_GUIDE.md",
    REPO_ROOT / ".github/workflows/validate-contribution.yml",
    REPO_ROOT / ".github/workflows/sweep-open-prs.yml",
)
ACTION_PATTERN = re.compile(
    r"(hashgraph-online/ai-plugin-scanner-action@)([0-9a-f]{40})"
    r"(?:\s+#\s+(v\d+\.\d+\.\d+))?"
)


def fetch_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return json.loads(response.read())


def resolve_commit(obj: dict[str, Any]) -> str:
    """Resolve a GitHub ref object, following annotated tags when necessary."""
    for _ in range(5):
        object_type = obj.get("type")
        sha = obj.get("sha")
        if object_type == "commit" and isinstance(sha, str) and re.fullmatch(
            r"[0-9a-f]{40}", sha
        ):
            return sha
        if object_type != "tag" or not isinstance(sha, str):
            break
        obj = fetch_json(f"{API_ROOT}/git/tags/{sha}")["object"]
    raise RuntimeError("Latest scanner action release did not resolve to a commit")


def fetch_latest_release() -> tuple[str, str]:
    release = fetch_json(f"{API_ROOT}/releases/latest")
    version = release.get("tag_name")
    if not isinstance(version, str) or not re.fullmatch(r"v\d+\.\d+\.\d+", version):
        raise RuntimeError("Latest scanner action release has an invalid tag")

    ref = fetch_json(f"{API_ROOT}/git/ref/tags/{version}")
    return version, resolve_commit(ref["object"])


def update_files(version: str, commit: str, *, check: bool) -> list[Path]:
    matches: list[tuple[Path, re.Match[str]]] = []
    contents: dict[Path, str] = {}
    for path in PINNED_FILES:
        content = path.read_text(encoding="utf-8")
        contents[path] = content
        matches.extend((path, match) for match in ACTION_PATTERN.finditer(content))

    if not matches:
        raise RuntimeError("No pinned scanner action uses were found")

    current_commits = {match.group(2) for _, match in matches}
    if len(current_commits) != 1:
        raise RuntimeError("Existing scanner action pins are inconsistent")

    replacement = rf"\g<1>{commit} # {version}"
    changed: list[Path] = []
    for path, content in contents.items():
        updated = ACTION_PATTERN.sub(replacement, content)
        if updated == content:
            continue
        changed.append(path)
        if not check:
            path.write_text(updated, encoding="utf-8")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Report drift only")
    args = parser.parse_args()

    try:
        version, commit = fetch_latest_release()
        changed = update_files(version, commit, check=args.check)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if changed:
        print("DRIFT" if args.check else f"Updated scanner action to {version} ({commit})")
        for path in changed:
            print(f"  {path.relative_to(REPO_ROOT)}")
    else:
        print(f"OK: scanner action is {version} ({commit})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
