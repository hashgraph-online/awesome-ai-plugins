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
# `plugins/<name>/.codex-plugin/` rather than the repo root. The Claude Code
# manifest is probed last on purpose: many repos ship both, and a repo that
# already resolves a Codex layout keeps that detected compatibility label.
INSTALL_PATH_CANDIDATES = (
    ".codex-plugin/plugin.json",
    "plugins/{repo}/.codex-plugin/plugin.json",
    ".codex/plugin.json",
    ".claude-plugin/plugin.json",
)
KIMI_MANIFEST_PATH_CANDIDATES = (
    "kimi.plugin.json",
    ".kimi-plugin/plugin.json",
    "plugin.json",
)
GROK_MANIFEST_PATH_CANDIDATES = (".grok-plugin/plugin.json",)
INSTALL_URL_PROBE_TIMEOUT = 6.0
DEEPSEEK_HARNESS_PLATFORM = "deepseek-harness"
GROK_PLATFORM = "grok"
KIMI_PLATFORM = "kimi"
MCP_PLATFORM = "mcp"
AGENT_SKILLS_PLATFORM = "agent-skills"

# `## Community Plugins` subsections such as `Development & Workflow` mix
# platforms, so the heading cannot classify their entries. Derive the platform
# from whichever manifest the probe actually resolved instead.
MANIFEST_PLATFORMS = {
    ".codex-plugin/plugin.json": "codex",
    ".codex/plugin.json": "codex",
    ".claude-plugin/plugin.json": "claude-code",
}

# Explicit platform sections use their own manifest paths. General community
# entries have no client-specific install target unless a supported manifest
# is actually found in the source repository.
PLATFORM_MANIFEST_PATHS = {
    "codex": ".codex-plugin/plugin.json",
    "claude-code": ".claude-plugin/plugin.json",
    GROK_PLATFORM: ".grok-plugin/plugin.json",
    KIMI_PLATFORM: "kimi.plugin.json",
}

# Community entries live under one README section. Keep the platform attached
# to each subsection so native Agent Skills, Grok, Kimi, and DeepSeek Harness
# entries are not misclassified as Codex plugins merely because catalogs share
# one section.
COMMUNITY_CATEGORY_PLATFORMS = {
    "Codex Plugins": "codex",
    "OpenAI Codex Plugins": "codex",
    "Agent Skills": AGENT_SKILLS_PLATFORM,
    "Grok Plugins": GROK_PLATFORM,
    "Kimi Plugins": KIMI_PLATFORM,
    "DeepSeek Harness Plugins": DEEPSEEK_HARNESS_PLATFORM,
}


def probe_install_url(
    owner: str,
    repo: str,
    candidates: tuple[str, ...] = INSTALL_PATH_CANDIDATES,
) -> str | None:
    """Return the first GitHub raw URL that resolves for a plugin manifest.

    Used for README-only additions so non-root manifest layouts don't end up
    with broken `install_url` values in the generated artifacts. Returns None
    on network errors or when no candidate exists; callers must not invent a
    client-specific install target when the manifest cannot be verified.
    """
    for template in candidates:
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
            # Network unavailable — callers may retain a documentation link,
            # but must not claim an unverified client-specific manifest.
            return None
    return None


CLAUDE_MARKETPLACE_PATH = ".claude-plugin/marketplace.json"
CLAUDE_MANIFEST_PATH = ".claude-plugin/plugin.json"
CLAUDE_MARKETPLACE_MAX_BYTES = 1024 * 1024


def probe_claude_marketplace(owner: str, repo: str, name: str) -> tuple[bool, str | None]:
    """Resolve a Claude Code plugin listed in the repo's root marketplace.

    A Claude Code marketplace repo keeps `.claude-plugin/marketplace.json` at
    the root and each plugin's manifest under the plugin's own directory, so
    the root manifest probe finds nothing. Read the marketplace, pick the
    plugin named like the README entry (or its only plugin), and resolve that
    plugin's manifest when its `source` is a path inside the repo and the
    manifest exists.

    Returns `(found, url)`: whether the repo has a root marketplace, and the
    raw URL of the plugin's manifest when it resolves. A marketplace that is
    found but does not resolve to exactly one manifest gives `(True, None)`;
    its repo has no root plugin manifest, so there is no install target.
    """
    base = f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD"
    try:
        with urllib.request.urlopen(
            f"{base}/{CLAUDE_MARKETPLACE_PATH}", timeout=INSTALL_URL_PROBE_TIMEOUT
        ) as resp:
            payload = resp.read(CLAUDE_MARKETPLACE_MAX_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError):
        return False, None
    try:
        if len(payload) > CLAUDE_MARKETPLACE_MAX_BYTES:
            return True, None
        marketplace = json.loads(payload.decode("utf-8"))
    except ValueError:
        return True, None

    entries = marketplace.get("plugins") if isinstance(marketplace, dict) else None
    if not isinstance(entries, list):
        return True, None
    entries = [e for e in entries if isinstance(e, dict)]
    named = [e for e in entries if str(e.get("name", "")).lower() == name.lower()]
    if len(named) == 1:
        entry = named[0]
    elif len(entries) == 1:
        entry = entries[0]
    else:
        return True, None

    # Only a directory inside this repo; remote sources (objects, URLs) have
    # their own manifests elsewhere.
    source = entry.get("source")
    if not isinstance(source, str) or "://" in source:
        return True, None
    parts = [part for part in source.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        return True, None

    manifest_path = "/".join([*parts, CLAUDE_MANIFEST_PATH])
    url = f"{base}/{manifest_path}"
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=INSTALL_URL_PROBE_TIMEOUT) as resp:
            if 200 <= resp.status < 300:
                return True, url
    except (urllib.error.URLError, TimeoutError, OSError):
        pass
    return True, None


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

    # Preserve source-catalog identity where it is explicit. An AI community
    # listing does not imply that the project targets Codex.
    upstream_source = str(plugin.get("source", "")).strip()
    if not entry.get("platform"):
        if upstream_source == "awesome-grok-plugins":
            entry["platform"] = GROK_PLATFORM
        elif upstream_source == "awesome-codex-plugins":
            entry["platform"] = "codex"

    ecosystems = entry.get("ecosystems")
    if not isinstance(ecosystems, list) or not ecosystems:
        if entry.get("platform"):
            entry["ecosystems"] = [entry["platform"]]
        else:
            entry.pop("ecosystems", None)

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

    normalized = []
    for plugin in plugins:
        if not isinstance(plugin, dict):
            continue
        entry = dict(plugin)
        entry.setdefault("platform", "codex")
        normalized.append(normalize_plugin(entry))

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
    }

    platform = str(plugin.get("platform", "")).strip()
    if platform:
        entry["platform"] = platform
    ecosystems = plugin.get("ecosystems")
    if isinstance(ecosystems, list) and ecosystems:
        entry["ecosystems"] = ecosystems
    elif platform:
        entry["ecosystems"] = [platform]

    for key in ("owner", "repo", "url", "install_url", "installation_url"):
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
    "Grok Plugins": GROK_PLATFORM,
    "Kimi Plugins": KIMI_PLATFORM,
    "DeepSeek Harness Plugins": DEEPSEEK_HARNESS_PLATFORM,
    # Community Plugins contains both platform-specific and ecosystem-neutral
    # entries. Its subsection, an actual manifest, or explicit metadata must
    # identify a client platform; the shared heading does not imply Codex.
    "Community Plugins": None,
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
    current_section = ""
    current_category = ""

    h2_re = re.compile(r"^##\s+(.+?)\s*$")
    h3_re = re.compile(r"^###\s+(.+?)\s*$")
    item_re = re.compile(r"^- \[([^\]]+)\]\(([^)]+)\)\s*[-–—]\s*(.+)")

    for line in content.split("\n"):
        h2 = h2_re.match(line)
        if h2:
            current_section = h2.group(1).strip()
            current_platform = PLATFORM_SECTIONS.get(current_section)
            current_category = ""
            continue

        h3 = h3_re.match(line.strip())
        if h3:
            current_category = h3.group(1).strip()
            if current_section == "Community Plugins":
                current_platform = COMMUNITY_CATEGORY_PLATFORMS.get(current_category)
            continue

        item = item_re.match(line.strip())
        if not item or (not current_platform and current_section != "Community Plugins"):
            continue

        name = item.group(1)
        url = item.group(2).strip()
        desc = item.group(3)
        standalone = desc.endswith(" <!-- platform: standalone -->")
        if standalone:
            desc = desc.removesuffix(" <!-- platform: standalone -->")
        desc = desc.rstrip(".")
        entry_platform = "standalone" if standalone else current_platform

        # github.com URLs only; relative paths (./plugins/...) are skipped.
        owner_match = re.match(
            r"https://github\.com/([^/]+)/([^/#?]+)",
            url.rstrip("/"),
        )
        if not owner_match:
            continue
        owner = owner_match.group(1)
        repo = owner_match.group(2).removesuffix(".git")

        # An explicit description prefix keeps native MCP servers in topical
        # categories without claiming they are client-specific plugin bundles.
        platform = MCP_PLATFORM if desc.startswith("MCP server: ") else entry_platform

        plugin = {
            "name": name,
            "url": url,
            "owner": owner,
            "repo": repo,
            "description": desc,
            "category": current_category,
            "source": "awesome-ai-plugins",
        }
        if platform:
            plugin["platform"] = platform
        manifest_path = PLATFORM_MANIFEST_PATHS.get(platform)
        if platform in {MCP_PLATFORM, AGENT_SKILLS_PLATFORM}:
            # Native MCP and Agent Skills entries use their source instructions,
            # not a client-specific plugin manifest.
            plugin["installation_url"] = f"https://github.com/{owner}/{repo}#readme"
        elif manifest_path:
            plugin["install_url"] = (
                "https://raw.githubusercontent.com/"
                f"{owner}/{repo}/HEAD/{manifest_path}"
            )
        elif platform in {DEEPSEEK_HARNESS_PLATFORM, "standalone"}:
            pass
        else:
            plugin["installation_url"] = f"https://github.com/{owner}/{repo}#readme"
        plugins.append(plugin)

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
        if not key:
            continue

        if key in seen:
            # A repository can support multiple runtimes. Preserve a native
            # runtime contribution when the same repository already exists in
            # an upstream or earlier README catalog entry.
            if plugin.get("platform") in {
                DEEPSEEK_HARNESS_PLATFORM,
                GROK_PLATFORM,
                KIMI_PLATFORM,
                MCP_PLATFORM,
                AGENT_SKILLS_PLATFORM,
            }:
                platform = plugin["platform"]
                for existing in (*upstream, *additions):
                    if normalize_repo_key(existing) != key:
                        continue
                    ecosystems = existing.get("ecosystems")
                    if not isinstance(ecosystems, list) or not ecosystems:
                        existing_platform = existing.get("platform")
                        ecosystems = [existing_platform] if existing_platform else []
                        existing["ecosystems"] = ecosystems
                    if not existing.get("platform"):
                        existing["platform"] = platform
                    if platform not in ecosystems:
                        ecosystems.append(platform)
                    if platform in {MCP_PLATFORM, AGENT_SKILLS_PLATFORM}:
                        existing["installation_url"] = plugin["installation_url"]
                    break
            continue

        seen.add(key)

        if plugin.get("platform") in {MCP_PLATFORM, AGENT_SKILLS_PLATFORM}:
            additions.append(normalize_plugin(plugin))
            continue

        if plugin.get("platform") in {DEEPSEEK_HARNESS_PLATFORM, "standalone"}:
            # Native runtimes resolve repository sources through their own
            # installers/manifests; never probe or substitute a Codex path.
            plugin.pop("install_url", None)
            additions.append(normalize_plugin(plugin))
            continue

        if plugin.get("platform") == KIMI_PLATFORM:
            probed = probe_install_url(
                plugin["owner"],
                plugin["repo"],
                KIMI_MANIFEST_PATH_CANDIDATES,
            )
            if probed:
                plugin["install_url"] = probed
            additions.append(normalize_plugin(plugin))
            continue

        if plugin.get("platform") == GROK_PLATFORM:
            probed = probe_install_url(
                plugin["owner"],
                plugin["repo"],
                GROK_MANIFEST_PATH_CANDIDATES,
            )
            if probed:
                plugin["install_url"] = probed
            else:
                plugin.pop("install_url", None)
            additions.append(normalize_plugin(plugin))
            continue

        probed = probe_install_url(plugin["owner"], plugin["repo"])
        if not probed:
            marketplace_found, probed = probe_claude_marketplace(
                plugin["owner"], plugin["repo"], plugin["name"]
            )
            if marketplace_found and not probed:
                # A Claude Code marketplace whose plugin can't be resolved:
                # any default manifest path would 404 or name another bundle.
                plugin.pop("install_url", None)
                plugin["platform"] = "claude-code"
                plugin["ecosystems"] = ["claude-code"]
                additions.append(normalize_plugin(plugin))
                continue
        if probed:
            plugin["install_url"] = probed
            plugin.pop("installation_url", None)
            for manifest_path, platform in MANIFEST_PLATFORMS.items():
                if probed.endswith(manifest_path):
                    plugin["platform"] = platform
                    plugin["ecosystems"] = [platform]
                    break
        # If no supported manifest resolves, the general listing remains
        # ecosystem-neutral and keeps its source README link only.

        additions.append(normalize_plugin(plugin))
    return upstream + additions, len(additions)


def generate_plugins_json(plugins: list[dict]) -> dict:
    """Generate plugins.json compatibility output."""
    platforms = {}
    for p in plugins:
        plat = p.get("platform")
        if not plat:
            continue
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
        # Keep regeneration deterministic within a day. A wall-clock
        # timestamp would make the sync workflow commit on every run and
        # retrigger itself indefinitely.
        "last_updated": datetime.date.today().isoformat(),
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
                        if GROK_PLATFORM not in ecos:
                            ecos.append(GROK_PLATFORM)
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
