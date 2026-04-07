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
from pathlib import Path

README = Path(__file__).parent.parent / "README.md"
OUTPUT = Path(__file__).parent.parent / "plugins.json"
MARKETPLACE_OUTPUT = Path(__file__).parent.parent / ".agents" / "plugins" / "marketplace.json"


def parse_plugins(readme_path: Path) -> dict:
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
                current_category = category_match.group(1)
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
    
    return plugins


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
    print(f"Reading README: {README}")
    plugins = parse_plugins(README)
    print(f"Found {len(plugins)} plugins")
    
    # Generate plugins.json
    plugins_json = generate_plugins_json(plugins)
    OUTPUT.write_text(json.dumps(plugins_json, indent=2) + "\n")
    print(f"Wrote: {OUTPUT}")
    
    # Generate marketplace.json
    marketplace = generate_marketplace_json(plugins)
    MARKETPLACE_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    MARKETPLACE_OUTPUT.write_text(json.dumps(marketplace, indent=2) + "\n")
    print(f"Wrote: {MARKETPLACE_OUTPUT}")
    
    print("Done!")


if __name__ == "__main__":
    main()
