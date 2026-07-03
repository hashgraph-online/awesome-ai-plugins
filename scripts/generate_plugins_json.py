#!/usr/bin/env python3
"""Regenerate compatibility metadata from README.

Usage:
    python3 scripts/generate_plugins_json.py

This script keeps artifacts aligned:
- plugins.json for compatibility
- .agents/plugins/marketplace.json for AI assistant marketplace installs
"""

from __future__ import annotations

import datetime
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

README = Path(__file__).parent.parent / "README.md"
OUTPUT = Path(__file__).parent.parent / "plugins.json"
MARKETPLACE_OUTPUT = Path(__file__).parent.parent / ".agents" / "plugins" / "marketplace.json"
CODEX_PLUGINS_JSON_URL = "https://raw.githubusercontent.com/hashgraph-online/awesome-codex-plugins/main/plugins.json"
GROK_PLUGINS_JSON_URL = "https://raw.githubusercontent.com/hashgraph-online/awesome-grok-plugins/main/plugins.json"
PINNED_PLUGIN_REPO = "hashgraph-online/registry-broker-codex-plugin"

# Candidate manifest paths inside a plugin repo, in priority order. The first
# template that resolves to an HTTP 200 wins. Some upstream entries (e.g.
# apple-productivity-mcp, yandex-direct-for-all) keep their manifest under
# `plugins/<name>/.codex-plugin/` rather than the repo root.
INSTALL_PATH_CANDIDATES = (
    ".codex-plugin/plugin.json",
    "plugins/{repo}/.codex-plugin/plugin.json",
    ".codex/plugin.json",
)
INSTALL_URL_PROBE_TIMEOUT = 6.0


def probe_install_url(owner: str, repo: str) -> str | None:
    """Return the first GitHub raw URL that resolves for a plugin manifest.

    Used for README-only additions so non-root manifest layouts don't end up
    with broken `install_url` values in the generated artifacts. Returns None
    on network errors or when no candidate exists; the caller decides whether
    to fall back to a default path or omit the field.
    """
    for template in INSTALL_PATH_CANDIDATES:
        path = template.format(repo=repo)
        url = (
            f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/{path}"
        )
        req = urllib.request.Request(url, method="HEAD")
        try:
            with urllib.request.urlopen(req, timeout=INSTALL_URL_PROBE_TIMEOUT) as resp:
                if 200 <= resp.status < 300:
                    return url
        except urllib.error.HTTPError:
            continue
        except (urllib.error.URLError, TimeoutError, OSError):
            # Network unavailable — caller will keep its default.
            return None
    return None


def normalize_repo_key(plugin: dict) -> str:
    owner = str(plugin.get("owner", "")).strip().rstrip("/").lower()
    repo = str(plugin.get("repo", "")).strip().rstrip("/").removesuffix(".git").lower()
    if owner and repo:
        return f"{owner}/{repo}"

    url = str(plugin.get("url", "")).strip().rstrip("/")
    match = re.match(r"https://github\.com/([^/]+)/([^/#?]+)", url)
    if match:
        return f"{match.group(1).lower()}/{match.group(2).removesuffix('.git').lower()}"

    return ""


def sort_plugins(plugins: list[dict]) -> list[dict]:
    """Keep HOL Registry Broker first, preserve upstream order for the rest."""
    return [
        plugin
        for _, plugin in sorted(
            enumerate(plugins),
            key=lambda item: (
                normalize_repo_key(item[1]) != PINNED_PLUGIN_REPO,
                item[0],
            ),
        )
    ]


def normalize_plugin(plugin: dict) -> dict:
    """Normalize upstream records for the multi-ecosystem registry feed."""
    entry = dict(plugin)
    entry["source"] = "awesome-ai-plugins"

    # Determine platform from upstream source field or fall back to codex
    upstream_source = str(plugin.get("source", "")).strip()
    if upstream_source == "awesome-grok-plugins":
        entry.setdefault("platform", "grok")
    else:
        entry.setdefault("platform", "codex")

    ecosystems = entry.get("ecosystems")
    if not isinstance(ecosystems, list) or not ecosystems:
        entry["ecosystems"] = [entry["platform"]]

    if normalize_repo_key(entry) == PINNED_PLUGIN_REPO:
        entry["featured"] = True

    return entry


def load_codex_plugins() -> list[dict]:
    """Load the current Codex plugin catalog used as the AI plugin seed."""
    with urllib.request.urlopen(CODEX_PLUGINS_JSON_URL, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"Upstream returned {response.status}")
        payload = json.loads(response.read().decode("utf-8"))

    if not isinstance(payload, dict):
        return []

    plugins = payload.get("plugins", [])
    if not isinstance(plugins, list):
        return []

    normalized = [
        normalize_plugin(plugin)
        for plugin in plugins
        if isinstance(plugin, dict)
    ]

    return sort_plugins(normalized)


def load_grok_plugins() -> list[dict]:
    """Load the current Grok plugin catalog."""
    try:
        with urllib.request.urlopen(GROK_PLUGINS_JSON_URL, timeout=30) as response:
            if response.status != 200:
                return []
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return []

    if not isinstance(payload, dict):
        return []

    plugins = payload.get("plugins", [])
    if not isinstance(plugins, list):
        return []

    normalized = [
        normalize_plugin(plugin)
        for plugin in plugins
        if isinstance(plugin, dict)
    ]

    return sort_plugins(normalized)


def marketplace_entry(plugin: dict) -> dict:
    """Build a portable marketplace entry without local plugin clones."""
    name = str(plugin.get("name", "")).strip()
    entry = {
        "name": name,
        "displayName": name,
        "description": plugin.get("description", ""),
        "category": plugin.get("category", ""),
        "source": "awesome-ai-plugins",
        "platform": plugin.get("platform", "codex"),
        "ecosystems": plugin.get("ecosystems", [plugin.get("platform", "codex")]),
    }

    for key in ("owner", "repo", "url", "install_url"):
        value = str(plugin.get(key, "")).strip()
        if value:
            entry[key] = value

    if plugin.get("featured") is True:
        entry["featured"] = True

    return entry


PLATFORM_SECTIONS = {
    "OpenAI Codex Plugins": "codex",
    "Claude Code Plugins": "claude-code",
    "OpenCode Plugins": "opencode",
    "Google Gemini CLI Plugins": "gemini-cli",
    "MCP Servers (Cross-Platform)": "mcp",
    # Current README structure: a single `## Community Plugins` section with
    # `###` subcategories. Treat its entries as codex marketplace plugins.
    "Community Plugins": "codex",
}


def parse_plugins(readme_path: Path) -> list[dict]:
    """Parse plugin entries from README by platform section.

    Recognized `## ...` headings (see PLATFORM_SECTIONS) put the parser into
    a plugin-collecting state. Any other `## ...` heading (Plugin Development,
    Guides & Articles, Related Projects, etc.) clears that state so their
    GitHub links are not misread as marketplace plugins.
    """
    content = readme_path.read_text(encoding="utf-8")

    plugins: list[dict] = []
    current_platform: str | None = None
    current_category = ""

    h2_re = re.compile(r"^##\s+(.+?)\s*$")
    h3_re = re.compile(r"^###\s+(.+?)\s*$")
    item_re = re.compile(r"^- \[([^\]]+)\]\(([^)]+)\)\s*[-–—]\s*(.+)")

    for line in content.split("\n"):
        h2 = h2_re.match(line)
        if h2:
            current_platform = PLATFORM_SECTIONS.get(h2.group(1).strip())
            current_category = ""
            continue

        h3 = h3_re.match(line.strip())
        if h3:
            current_category = h3.group(1)
            continue

        item = item_re.match(line.strip())
        if not (item and current_platform):
            continue

        name = item.group(1)
        url = item.group(2).strip()
        desc = item.group(3).rstrip(".")

        # github.com URLs only; relative paths (./plugins/...) are skipped.
        owner_match = re.match(
            r"https://github\.com/([^/]+)/([^/#?]+)",
            url.rstrip("/"),
        )
        if not owner_match:
            continue
        owner = owner_match.group(1)
        repo = owner_match.group(2).removesuffix(".git")

        plugins.append({
            "name": name,
            "url": url,
            "owner": owner,
            "repo": repo,
            "description": desc,
            "category": current_category,
            "platform": current_platform,
            "source": "awesome-ai-plugins",
            "install_url": (
                "https://raw.githubusercontent.com/"
                f"{owner}/{repo}/HEAD/.codex-plugin/plugin.json"
            ),
        })

    return sort_plugins(plugins)


def merge_readme_additions(
    upstream: list[dict], readme_path: Path
) -> tuple[list[dict], int]:
    """Append README plugin entries not already present in `upstream`.

    The upstream Codex catalog is the primary source. Plugins added directly
    to this repo's README (without a corresponding submission upstream) would
    otherwise never reach plugins.json or marketplace.json. This merges them
    in, deduplicated by owner/repo, and probes for the manifest location so
    plugins with non-root layouts get a working `install_url` instead of a
    hardcoded path that 404s.
    """
    seen = {key for key in (normalize_repo_key(p) for p in upstream) if key}
    additions: list[dict] = []
    for plugin in parse_plugins(readme_path):
        key = normalize_repo_key(plugin)
        if not key or key in seen:
            continue
        seen.add(key)

        probed = probe_install_url(plugin["owner"], plugin["repo"])
        if probed:
            plugin["install_url"] = probed
        else:
            # Probe returned None (no candidate matched, or network down).
            # Keep the default install_url; flag for the operator so a broken
            # path can be hand-corrected upstream if needed.
            print(
                f"WARN: could not verify manifest for {plugin['name']} "
                f"({plugin['owner']}/{plugin['repo']}); "
                f"keeping default install_url"
            )

        additions.append(normalize_plugin(plugin))
    return upstream + additions, len(additions)


def generate_plugins_json(plugins: list[dict]) -> dict:
    """Generate plugins.json compatibility output."""
    platforms = {}
    for p in plugins:
        plat = p.get("platform", "unknown")
        if plat not in platforms:
            platforms[plat] = []
        platforms[plat].append(p)
    
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "name": "awesome-ai-plugins",
        "version": "1.0.0",
        "last_updated": datetime.date.today().isoformat(),
        "total": len(plugins),
        "platforms": list(platforms.keys()),
        "plugins": plugins,
    }


def generate_marketplace_json(plugins: list[dict]) -> dict:
    """Generate marketplace.json for AI assistant marketplace installs."""
    return {
        "schema_version": "1.0",
        "source": "awesome-ai-plugins",
        "last_updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "plugins": plugins,
    }


def main():
    print(f"Reading upstream: {CODEX_PLUGINS_JSON_URL}")
    try:
        plugins = load_codex_plugins()
    except Exception as exc:
        print(f"Upstream feed unavailable, falling back to README: {exc}")
        plugins = parse_plugins(README)
    print(f"Found {len(plugins)} plugins from upstream catalog")

    # Merge Grok plugins from awesome-grok-plugins upstream
    grok_plugins = load_grok_plugins()
    if grok_plugins:
        print(f"Found {len(grok_plugins)} plugins from grok upstream catalog")
        # Deduplicate by owner/repo: grok plugins that also exist in codex
        # are tagged with both ecosystems rather than duplicated
        existing_keys = {normalize_repo_key(p) for p in plugins}
        for gp in grok_plugins:
            key = normalize_repo_key(gp)
            if key and key in existing_keys:
                # Plugin exists in both ecosystems: add grok to ecosystems
                for existing in plugins:
                    if normalize_repo_key(existing) == key:
                        ecos = existing.get("ecosystems", [])
                        if "grok" not in ecos:
                            ecos.append("grok")
                            existing["ecosystems"] = ecos
                        break
            else:
                plugins.append(gp)
        print(f"Total after grok merge: {len(plugins)}")
    else:
        print("No grok plugins found upstream (repo may be empty or unavailable)")

    plugins, added = merge_readme_additions(plugins, README)
    if added:
        print(f"Merged {added} README-only addition(s) from {README.name}")
    print(f"Total plugins after merge: {len(plugins)}")

    if not plugins:
        raise SystemExit("No plugins found; refusing to write empty registry feeds")
    
    # Generate plugins.json
    plugins_json = generate_plugins_json(plugins)
    OUTPUT.write_text(json.dumps(plugins_json, indent=2) + "\n")
    print(f"Wrote: {OUTPUT}")
    
    # Generate marketplace.json
    marketplace = generate_marketplace_json(
        [marketplace_entry(plugin) for plugin in plugins]
    )
    MARKETPLACE_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    MARKETPLACE_OUTPUT.write_text(json.dumps(marketplace, indent=2) + "\n")
    print(f"Wrote: {MARKETPLACE_OUTPUT}")
    
    print("Done!")


if __name__ == "__main__":
    main()
