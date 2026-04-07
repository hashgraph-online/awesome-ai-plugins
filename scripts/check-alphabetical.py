#!/usr/bin/env python3
"""Linter: verify that list entries within each section of README.md are alphabetically sorted.

Supports pinned entries: any list item preceded by `<!-- pinned -->` is treated as
always-first and excluded from alphabetical checking. The remaining items must be sorted.
"""

import re
import sys
from pathlib import Path


def extract_sections(filepath: str) -> list[tuple[str, str, list[str]]]:
    """Extract sections and their list items from a markdown file.

    Returns list of (section_heading, context, items) where items are the display text.
    """
    content = Path(filepath).read_text()
    lines = content.split("\n")

    sections = []
    current_heading = None
    current_items: list[tuple[str, int]] = []  # (display_text, line_number)
    # Regex for markdown list items with links: "- [Display Text](url) - description"
    # Also handles plain list items: "- Display Text - description"
    item_re = re.compile(r"^- \[([^\]]+)\]\([^)]+\)", re.IGNORECASE)
    for i, line in enumerate(lines, 1):
        # Detect section headers (## or ###)
        heading_match = re.match(r"^(#{2,3})\s+(.+)", line)
        # Also detect <summary> tags as section boundaries inside <details>
        summary_match = re.match(r"<summary>(.+)</summary>", line.strip())

        if heading_match:
            # Save previous section if it has items
            if current_heading and current_items:
                sections.append((current_heading, [t for t, _ in current_items]))
            current_heading = heading_match.group(2).strip()
            current_items = []
        elif summary_match:
            if current_heading and current_items:
                sections.append((current_heading, [t for t, _ in current_items]))
            current_heading = f"[{summary_match.group(1).strip()}]"
            current_items = []
        elif item_re.match(line):
            display_text = item_re.match(line).group(1)
            # Skip pinned entries
            if i > 1 and re.search(r"<!--\s*pinned\s*-->", lines[i - 2]):
                continue
            current_items.append((display_text.lower(), i))

    # Don't forget the last section
    if current_heading and current_items:
        sections.append((current_heading, [t for t, _ in current_items]))

    return sections


def check_sorted(items: list[str]) -> bool:
    """Return True if items are alphabetically sorted."""
    return all(items[i] <= items[i + 1] for i in range(len(items) - 1))


def main():
    readme = sys.argv[1] if len(sys.argv) > 1 else "README.md"

    if not Path(readme).exists():
        print(f"ERROR: {readme} not found")
        sys.exit(1)

    sections = extract_sections(readme)
    errors = 0

    skip_sections = {"Contents"}  # TOC follows document order, not alphabetical

    for heading, items in sections:
        if not items or heading in skip_sections:
            continue
        if not check_sorted(items):
            sorted_items = sorted(items)
            print(f"FAIL: Section '{heading}' is not alphabetically sorted.")
            print(f"  Current order: {', '.join(items)}")
            print(f"  Expected order: {', '.join(sorted_items)}")
            errors += 1
        else:
            print(f"OK:   '{heading}' ({len(items)} items)")

    if errors:
        print(f"\n{errors} section(s) failed alphabetical check.")
        sys.exit(1)
    else:
        print("\nAll sections are alphabetically sorted.")
        sys.exit(0)


if __name__ == "__main__":
    main()
