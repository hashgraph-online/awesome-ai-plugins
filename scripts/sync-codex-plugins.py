#!/usr/bin/env python3
"""Sync ONLY NEW Codex plugins from awesome-codex-plugins into awesome-ai-plugins."""

import re
import sys
from pathlib import Path


def extract_plugin_urls(readme: Path) -> set:
    """Extract all plugin URLs from a README."""
    content = readme.read_text(encoding="utf-8")
    urls = set()
    for line in content.split("\n"):
        match = re.match(r"^- \[.+\]\(([^)]+)\)", line.strip())
        if match:
            urls.add(match.group(1))
    return urls


def extract_new_plugins(codex_readme: Path, existing_urls: set) -> list[str]:
    """Extract plugin entries from codex that don't exist in ai-plugins."""
    content = codex_readme.read_text(encoding="utf-8")
    lines = content.split("\n")
    
    in_community = False
    new_entries = []
    
    for line in lines:
        stripped = line.strip()
        
        if stripped == "## Community Plugins":
            in_community = True
            continue
            
        if in_community:
            if stripped.startswith("---") or (stripped.startswith("## ") and "Community" not in stripped):
                break
                
            match = re.match(r"^- \[([^\]]+)\]\(([^)]+)\)\s*[-–—]\s*(.+)", stripped)
            if match:
                url = match.group(2)
                if url not in existing_urls:
                    new_entries.append(stripped)
    
    return new_entries


def append_entries(readme: Path, new_entries: list[str]) -> bool:
    """Append new entries to existing plugin list in the ### Community Plugins section."""
    if not new_entries:
        return False
        
    content = readme.read_text(encoding="utf-8")
    lines = content.split("\n")
    
    new_lines = []
    in_section = False
    existing = []
    
    for line in lines:
        stripped = line.strip()
        
        if stripped == "### Community Plugins":
            in_section = True
            new_lines.append(line)
            continue
            
        if in_section:
            if stripped.startswith("---") or stripped.startswith("## "):
                in_section = False
                # First add existing entries
                new_lines.extend(existing)
                # Then add new entries sorted
                new_entries.sort(key=lambda e: re.sub(r"^- \[", "", e).lower())
                new_lines.extend(new_entries)
                new_lines.append("")
                new_lines.append(line)
                continue
                
            if stripped.startswith("- ["):
                existing.append(stripped)
                continue
                
        new_lines.append(line)
    
    new_content = "\n".join(new_lines)
    if new_content != content:
        readme.write_text(new_content, encoding="utf-8")
        return True
    return False


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/sync-codex-plugins.py <path-to-codex-repo>")
        sys.exit(1)

    codex_readme = Path(sys.argv[1]) / "README.md"
    ai_readme = Path(__file__).parent.parent / "README.md"

    # Get existing URLs from ai-plugins
    existing_urls = extract_plugin_urls(ai_readme)
    print(f"Already have {len(existing_urls)} plugins")
    
    # Find new ones from codex
    new_entries = extract_new_plugins(codex_readme, existing_urls)
    print(f"Found {len(new_entries)} NEW Codex plugins to add")
    
    if new_entries:
        # Also extract existing entries to preserve
        existing_entries = []
        content = ai_readme.read_text(encoding="utf-8")
        in_section = False
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped == "### Community Plugins":
                in_section = True
                continue
            if in_section:
                if stripped.startswith("---") or stripped.startswith("## "):
                    break
                if stripped.startswith("- ["):
                    existing_entries.append(stripped)
        
        changed = append_entries(ai_readme, new_entries)
        if changed:
            print(f"Updated {ai_readme}")
        else:
            print("No changes needed")
    else:
        print("No new plugins to add")


if __name__ == "__main__":
    main()
