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
import urllib.request
from pathlib import Path

README = Path(__file__).parent.parent / "README.md"
OUTPUT = Path(__file__).parent.parent / "plugins.json"
MARKETPLACE_OUTPUT = Path(__file__).parent.parent / ".agents" / "plugins" / "marketplace.json"
CODEX_PLUGINS_JSON_URL = "https://raw.githubusercontent.com/hashgraph-online/awesome-codex-plugins/main/plugins.json"
PINNED_PLUGIN_REPO = "hashgraph-online/registry-broker-codex-plugin"


def normalize_repo_key(plugin: dict) -> str:
    owner = str(plugin.get("owner", "")).strip().lower()
    repo = str(plugin.get("repo", "")).strip().lower()
    if owner and repo:
        return f"{owner}/{repo}"

    url = str(plugin.get("url", "")).strip()
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
        payload = json.loads(response.read().decode("utf-8"))

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
    entry = {
        "name": plugin.get("name"),
        "displayName": plugin.get("name"),
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


def parse_plugins(readme_path: Path) -> list[dict]:
    """Parse plugins from README by platform section."""
    content = readme_path.read_text(encoding="utf-8")
    
    # Define platform sections to parse
    platforms = {
        "OpenAI Codex Plugins": "codex",
        "Claude Code Plugins": "claude-code",
        "OpenCode Plugins": "opencode",
        "Google Gemini CLI Plugins": "gemini-cli",
        "MCP Servers (Cross-Platform)": "mcp",
    }
    
    plugins = []
    current_platform = None
    
    for line in content.split("\n"):
        # Check for platform headers
        for platform_name, platform_id in platforms.items():
            if line.strip() == f"## {platform_name}":
                current_platform = platform_id
                break
        else:
            # Check for subcategory headers (###)
            category_match = re.match(r"^### (.+)", line.strip())
            if category_match:
                continue
            
            # Parse plugin entries: - [Name](url) - Description
            plugin_match = re.match(
                r"^- \[([^\]]+)\]\(([^)]+)\)\s*[-–]\s*(.+)",
                line.strip(),
            )
            if plugin_match and current_platform:
                name = plugin_match.group(1)
                url = plugin_match.group(2)
                desc = plugin_match.group(3).rstrip(".")
                owner = ""
                repo = ""
                
                # Extract owner/repo from github.com URLs
                owner_match = re.match(r"https://github\.com/([^/]+)/([^/]+)", url)
                if owner_match:
                    owner = owner_match.group(1)
                    repo = owner_match.group(2)
                
                plugins.append({
                    "name": name,
                    "url": url,
                    "owner": owner,
                    "repo": repo,
                    "description": desc,
                    "platform": current_platform,
                    "source": "awesome-ai-plugins",
                })
    
    return sort_plugins(plugins)


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
    print(f"Found {len(plugins)} plugins")

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
