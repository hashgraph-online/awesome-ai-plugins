#!/usr/bin/env python3
"""Backfill missing claim notices for merged catalog contribution PRs."""

import importlib.util
import os
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

SCRIPT_DIR = Path(__file__).resolve().parent
NOTICE_PATH = SCRIPT_DIR / "post-claim-notice.py"
SPEC = importlib.util.spec_from_file_location("post_claim_notice", NOTICE_PATH)
NOTICE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(NOTICE)


def parse_max_prs(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError("BACKFILL_MAX_PRS must be an integer") from error
    if value < 1 or value > 1000:
        raise ValueError("BACKFILL_MAX_PRS must be between 1 and 1000")
    return value


def list_merged_prs(repository: str, token: str, since: str, max_prs: int):
    query = f"repo:{repository} is:pr is:merged merged:>={since}"
    headers = {"Authorization": f"token {token}"}
    page = 1
    collected = []

    while len(collected) < max_prs:
        url = "https://api.github.com/search/issues?" + urlencode(
            {"q": query, "sort": "created", "order": "asc", "per_page": 100, "page": page}
        )
        data = NOTICE.api_request(url, headers=headers)
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise RuntimeError("GitHub could not list merged pull requests")
        if data.get("incomplete_results") is True:
            raise RuntimeError("GitHub returned incomplete merged pull request results")
        if page == 1 and data.get("total_count", 0) > max_prs:
            raise RuntimeError(
                f"merged PR count exceeds BACKFILL_MAX_PRS ({max_prs})"
            )
        items = data["items"]
        collected.extend(items)
        if len(items) < 100:
            return collected
        page += 1

    return collected


def main():
    token = os.environ.get("GH_TOKEN", "")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    since = os.environ.get("BACKFILL_SINCE") or (
        datetime.now(timezone.utc) - timedelta(days=7)
    ).date().isoformat()
    if not token or not repository:
        print("Error: GH_TOKEN and GITHUB_REPOSITORY are required", file=sys.stderr)
        return 1

    try:
        max_prs = parse_max_prs(os.environ.get("BACKFILL_MAX_PRS", "1000"))
        pull_requests = list_merged_prs(repository, token, since, max_prs)
    except (RuntimeError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print(f"Backfilling {len(pull_requests)} merged PRs since {since}")
    NOTICE.GH_TOKEN = token
    NOTICE.REPO_FULL = repository
    failures = []

    for pull_request in pull_requests:
        number = pull_request.get("number")
        title = pull_request.get("title")
        user = pull_request.get("user")
        author = user.get("login") if isinstance(user, dict) else None
        if not isinstance(number, int) or not isinstance(title, str) or not isinstance(author, str):
            print("Error: GitHub returned incomplete pull request metadata", file=sys.stderr)
            failures.append(str(number))
            continue
        NOTICE.PR_NUMBER = str(number)
        NOTICE.PR_TITLE = title
        NOTICE.PR_AUTHOR = author
        result = NOTICE.main()
        # Keep comment writes and optional ownership queries below burst limits.
        time.sleep(1.25)
        if result == NOTICE.NOTICE_DEFERRED:
            print(f"::warning::Claim notice backfill deferred at PR #{number}; a later backfill will retry it.")
            failures.append(str(number))
            continue
        if result != 0:
            print(f"Error: claim notice backfill failed at PR #{number}", file=sys.stderr)
            failures.append(str(number))

    if failures:
        print(f"::error::Claim notices need retry for PRs: {', '.join(failures)}")
        return 1

    print("Claim notice backfill completed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
