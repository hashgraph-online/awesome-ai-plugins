#!/usr/bin/env python3
"""
Post a claim notice on merged PRs that add plugins to the HOL catalog source.

Direct claim links are emitted only for repositories already present in the live
HOL Registry. Repositories that are merged into README.md but still waiting for
Registry ingestion are called out as syncing so creators are not sent into a
claim flow that cannot resolve their plugin yet.
"""

import json
import os
import re
import sys
import subprocess
import urllib.request
import urllib.error
from urllib.parse import urlencode

# --- Config ---
REGISTRY_API = os.environ.get("REGISTRY_API", "https://hol.org/registry/api/v1")
GH_TOKEN = os.environ.get("GH_TOKEN", "")
PR_NUMBER = os.environ.get("PR_NUMBER", "")
PR_TITLE = os.environ.get("PR_TITLE", "")
PR_AUTHOR = os.environ.get("PR_AUTHOR", "")
REPO_FULL = os.environ.get("GITHUB_REPOSITORY", "")
MAX_CATALOG_PAGES = 10

# Skip titles that aren't new plugin additions
SKIP_PATTERNS = [
    r"^docs?:",
    r"^fix\b",
    r"^ci\b",
    r"^chore\b",
    r"^refactor\b",
    r"^test\b",
    r"^build\b",
    r"Add icon for",
    r"Add .* icon",
    r"Add .* marketplace icon",
    r"For praxis, add marketplace icon",
    r"Update .*(listing|plugin owner|description|to v|trust signals)",
    r"Sync ",
    r"#\d+ Fix ",
    r"Fix install_url",
    r"Fix Casefile README",
    r"Canvas-Apps-Plugin-Codex - Update",
    r"docs: reframe scanner",
    r"docs: add PANews",
    r"fix\(registry\)",
    r"fix\(readme\)",
    r"fix\(plugins\)",
    r"feat: Make HOL Plugin Scanner",
    r"feat: publish curated marketplace",
    r"feat: add HOL Guard Plugin",
    r"ci\(sync\)",
    r"ci\(workflows\)",
    r"Add HOL Guard scanner",
]


class RegistryCatalogFetchError(RuntimeError):
    """Raised when Registry catalog evidence is unavailable or incomplete."""


def build_comment_body(author: str, repositories=(), pending_repositories=()) -> str:
    """Build the claim notice comment body, tagging the PR author.

    `repositories` must contain only repos that are confirmed in the live HOL
    Registry. `pending_repositories` may contain repos confirmed in the merged
    catalog source but not yet present in the Registry.
    """
    claimable = sorted(set(repositories))
    pending = sorted(set(pending_repositories) - set(claimable))

    sections = []
    if claimable:
        claim_links = "\n".join(
            f"- [Verify ownership of `{repo}`](https://hol.org/guard/plugins?{urlencode({'claim': repo, 'utm_source': 'github', 'utm_medium': 'pr_comment', 'utm_campaign': 'plugin_claim', 'utm_content': 'merge_notice'})})"
            for repo in claimable
        )
        sections.append(f"""### How to claim

{claim_links}

1. Open your plugin's link above, then choose **\"Continue with GitHub\"**. Your plugin stays selected through sign-in.
2. Use the GitHub account that maintains the repository. We request only `read:user` and `user:email`, with no repository write access.
3. Complete ownership verification to receive the owner-verified badge. Inconclusive repository permissions may require review.""")

    if pending:
        pending_lines = "\n".join(f"- `{repo}`" for repo in pending)
        sections.append(f"""### Still syncing

These repositories are merged into HOL's catalog source but are not live in the HOL Registry yet:

{pending_lines}

No action is needed yet. The claim link will work after the listing appears in the Registry.""")

    if not sections:
        sections.append("[Open the plugin dashboard](https://hol.org/guard/plugins)")

    action_sections = "\n\n".join(sections)
    return f"""<!-- hol-claim-notice -->
🎉 Hey @{author}, this plugin submission has been merged into HOL's catalog source.

## Claim your plugin

Once the plugin appears in the [HOL Registry](https://hol.org/plugins), if you maintain it, you can verify ownership to unlock:

- **Owner-verified badge** on your plugin's registry listing
- **Trust score** visibility and analytics for your plugin
- **Direct claim link** to share with your community
- **Dashboard access** at [hol.org/guard/plugins](https://hol.org/guard/plugins) to track installs, trust, and engagement

{action_sections}

No need to add any secrets or tokens to your repo. Ownership verification is done entirely through GitHub OAuth.

If you have any questions, feel free to ask here or reach out at [support@hol.org](mailto:support@hol.org)."""


MARKER = "<!-- hol-claim-notice -->"


def api_request(url, headers=None, method="GET", data=None):
    """Make an HTTP request and return parsed JSON."""
    req_headers = {"Accept": "application/json", "User-Agent": "hol-claim-notice/1.0"}
    if headers:
        req_headers.update(headers)
    if data is not None:
        data = json.dumps(data).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, headers=req_headers, method=method, data=data)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"  HTTP {e.code} from {url}: {body[:200]}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  Error fetching {url}: {e}", file=sys.stderr)
        return None


def should_skip_title(title: str) -> bool:
    """Return True if the PR title matches a non-plugin pattern."""
    # `docs: add <plugin>` is a common legitimate contribution title. Allow it
    # through the title gate, while retaining specific docs-only exclusions below.
    allow_docs_add = bool(re.match(r"^docs?:\s+add\b", title.strip(), re.IGNORECASE))
    for pattern in SKIP_PATTERNS:
        if pattern == r"^docs?:" and allow_docs_add:
            continue
        if re.search(pattern, title, re.IGNORECASE):
            return True
    return False


def fetch_catalog_repos(owner_verified: bool = False):
    """Fetch a complete Registry catalog repo set.

    Missing/failed pages and pagination that exceeds the configured bound are
    errors, not evidence that a repository is absent. This prevents transient
    Registry failures from producing irreversible claim-notice markers.
    """
    repos = set()
    cursor = None
    base_url = f"{REGISTRY_API}/plugins/catalog?limit=50"
    if owner_verified:
        base_url += "&ownerVerified=true"

    for page_index in range(MAX_CATALOG_PAGES):
        url = base_url if not cursor else f"{base_url}&cursor={cursor}"
        data = api_request(url)
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise RegistryCatalogFetchError(
                f"registry catalog page {page_index + 1} unavailable"
            )

        for plugin in data["items"]:
            if not isinstance(plugin, dict):
                continue
            repo = plugin.get("sourceRepo") or plugin.get("repository") or ""
            if not isinstance(repo, str):
                continue
            repo = repo.replace("https://github.com/", "").strip()
            if repo:
                repos.add(repo.lower())

        cursor = data.get("nextCursor")
        if not cursor:
            return repos

    raise RegistryCatalogFetchError(
        f"registry catalog exceeded {MAX_CATALOG_PAGES} pages"
    )


def normalize_repo_url(raw: str) -> str:
    """Normalize a GitHub URL or 'owner/repo' string to lowercase 'owner/repo'.

    Strips trailing slashes, .git suffix, and extra path segments.
    """
    s = raw.strip().rstrip("/")
    if s.endswith(".git"):
        s = s[:-4]
    # If it's a full URL, extract owner/repo
    match = re.match(r"https?://github\.com/([^/]+/[^/]+)", s, re.IGNORECASE)
    if match:
        s = match.group(1)
    else:
        # Handle bare 'owner/repo/extra/path' — take first two segments
        parts = s.split("/")
        if len(parts) >= 2:
            s = f"{parts[0]}/{parts[1]}"
    return s.lower()


def parse_pr_diff_for_repos():
    """Get the PR diff and extract GitHub repo URLs from added lines."""
    result = subprocess.run(
        ["gh", "pr", "diff", PR_NUMBER, "--repo", REPO_FULL],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "GH_TOKEN": GH_TOKEN},
        check=True,
    )
    diff = result.stdout

    repos = set()
    # Match https://github.com/owner/repo in added lines (starting with +)
    for line in diff.split("\n"):
        if not line.startswith("+"):
            continue
        matches = re.findall(r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)", line)
        for match in matches:
            normalized = normalize_repo_url(match)
            # Skip hashgraph-online repos (our own)
            if not normalized.startswith("hashgraph-online/"):
                repos.add(normalized)

    return repos


def has_existing_claim_comment():
    """Check if the PR already has a claim-notice or manual claim comment."""
    url = f"https://api.github.com/repos/{REPO_FULL}/issues/{PR_NUMBER}/comments"
    headers = {"Authorization": f"token {GH_TOKEN}"}
    comments = api_request(url, headers=headers)
    if not isinstance(comments, list):
        return False
    for comment in comments:
        body = comment.get("body") or ""
        if MARKER in body:
            return True
        # Also detect manual claim comments posted before automation
        if "Claim your plugin" in body and "hol.org/guard/plugins" in body:
            return True
    return False


def post_comment(author: str, repositories=(), pending_repositories=()):
    """Post the claim notice comment on the PR, tagging the author."""
    url = f"https://api.github.com/repos/{REPO_FULL}/issues/{PR_NUMBER}/comments"
    headers = {"Authorization": f"token {GH_TOKEN}"}
    body = build_comment_body(author, repositories, pending_repositories)
    result = api_request(url, headers=headers, method="POST", data={"body": body})
    return result is not None


def main():
    # Validate required environment variables
    missing = []
    if not GH_TOKEN:
        missing.append("GH_TOKEN")
    if not PR_NUMBER:
        missing.append("PR_NUMBER")
    if not REPO_FULL:
        missing.append("GITHUB_REPOSITORY")
    if missing:
        print(f"Error: missing required environment variables: {', '.join(missing)}", file=sys.stderr)
        return 1

    print(f'PR #{PR_NUMBER}: "{PR_TITLE}" by @{PR_AUTHOR}')

    # 1. Skip non-plugin PRs
    if should_skip_title(PR_TITLE):
        print("  Skipping: non-plugin PR title pattern")
        return 0

    if PR_AUTHOR in ("kantorcodes", "github-actions[bot]"):
        print("  Skipping: bot/owner PR")
        return 0

    # 2. Check for existing claim comment
    if has_existing_claim_comment():
        print("  Skipping: claim notice already posted")
        return 0

    # 3. Parse PR diff for GitHub repo URLs
    try:
        pr_repos = parse_pr_diff_for_repos()
    except subprocess.CalledProcessError as e:
        print(f"  Failed to get PR diff (exit {e.returncode}): {e.stderr}", file=sys.stderr)
        return 0
    except subprocess.TimeoutExpired:
        print("  Failed to get PR diff: timed out", file=sys.stderr)
        return 0

    if not pr_repos:
        print("  Skipping: no GitHub repo URLs found in PR diff")
        return 0

    print(f"  Found repos in diff: {', '.join(pr_repos)}")

    # 4. Fetch a complete Registry snapshot. A failed/partial snapshot is not
    # evidence that a repository is still syncing, so fail closed without
    # posting a marker and allow a later workflow dispatch to recover.
    print("  Fetching registry catalog...")
    try:
        registry_repos = fetch_catalog_repos(owner_verified=False)
    except RegistryCatalogFetchError as error:
        print(f"  Skipping: Registry catalog unavailable ({error})", file=sys.stderr)
        return 0
    print(f"  Registry has {len(registry_repos)} plugins")

    # 5. Split live Registry repos from catalog-source repos that are still syncing.
    live_repos = pr_repos & registry_repos
    pending_repos = set()
    missing_from_registry = pr_repos - live_repos
    if missing_from_registry:
        print("  Some repos are not in the registry yet, checking local README.md...")
        readme_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "README.md")
        if os.path.exists(readme_path):
            readme_content = open(readme_path, encoding="utf-8").read().lower()
            pending_repos = {
                repo for repo in missing_from_registry if repo.lower() in readme_content
            }
            if pending_repos:
                print(f"  Pending registry sync: {', '.join(sorted(pending_repos))}")

    if not live_repos and not pending_repos:
        print("  Skipping: none of the PR repos are in the registry or merged catalog source")
        return 0

    if live_repos:
        print(f"  Live in registry: {', '.join(sorted(live_repos))}")

    # 6. Check owner verification only for repos actually live in the Registry.
    already_verified = set()
    if live_repos:
        print("  Checking owner verification status...")
        try:
            verified_repos = fetch_catalog_repos(owner_verified=True)
        except RegistryCatalogFetchError as error:
            print(
                f"  Skipping: owner-verification catalog unavailable ({error})",
                file=sys.stderr,
            )
            return 0
        already_verified = live_repos & verified_repos

    claimable_repos = live_repos - already_verified
    if already_verified:
        print(f"  Already verified: {', '.join(sorted(already_verified))}")

    if not claimable_repos and not pending_repos:
        print("  Skipping: all live matched repos are already owner-verified")
        return 0

    # 7. Post the comment. Pending repos never receive a direct claim URL.
    print("  Posting claim notice comment...")
    if post_comment(PR_AUTHOR, claimable_repos, pending_repos):
        print("  ✅ Comment posted successfully")
        return 0

    print("  ❌ Failed to post comment")
    return 1


if __name__ == "__main__":
    sys.exit(main())
