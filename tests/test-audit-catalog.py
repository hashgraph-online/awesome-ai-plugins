import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("audit_catalog", ROOT / "scripts/audit-catalog.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class AuditCatalogTests(unittest.TestCase):
    def test_reads_only_community_plugin_entries_and_preserves_location(self):
        validator = type("Validator", (), {
            "README_ENTRY_RE": __import__("re").compile(
                r"^- \[([^\]]+)\]\((https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+))\)\s*-\s*(.+)$"
            )
        })
        readme = """## Contents
- [Index](https://github.com/example/index) - navigation

## Community Plugins
### Agent Skills
- [Example](https://github.com/example/skill) - skill <!-- platform: agent-skills -->
"""
        entries = MODULE.read_catalog_entries(readme, validator)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].subsection, "Agent Skills")
        self.assertEqual(entries[0].line, 6)
        self.assertEqual(MODULE.platform_marker(entries[0]), "agent-skills")

    def test_guard_signals_are_advisory_and_normalized(self):
        signals = MODULE.guard_signals(
            "Verifies writes against the system of record and emits signed receipts."
        )
        self.assertEqual(
            signals,
            ("system-of-record", "write-verification", "receipts"),
        )

    def test_api_unavailability_does_not_look_like_a_catalog_defect(self):
        self.assertEqual(MODULE.error_status("GitHub returned HTTP 429"), "unavailable")
        self.assertEqual(MODULE.error_status("response is larger than 4194304 bytes"), "unavailable")
        self.assertEqual(
            MODULE.error_status("https://github.com/example/repo does not expose a recognized AI extension surface"),
            "needs-review",
        )
        self.assertEqual(
            MODULE.error_status("https://github.com/example/repo exposes an Agent Skills extension"),
            "needs-reclassification",
        )

    def test_markdown_report_lists_review_and_guard_candidates(self):
        entry = MODULE.CatalogEntry(
            "Example", "https://github.com/example/repo", "example", "repo",
            "description", "Community Plugins", "Tools", 10,
        )
        results = [
            MODULE.AuditResult(
                entry.display_name, entry.url, entry.owner, entry.repo,
                entry.section, entry.subsection, entry.line,
                None, "needs-review", error="no surface",
            ),
            MODULE.AuditResult(
                entry.display_name, entry.url, entry.owner, entry.repo,
                entry.section, entry.subsection, entry.line,
                "mcp", "eligible", guard_candidate=True, guard_signals=("receipts",),
            ),
        ]
        report = MODULE.markdown_report(results)
        self.assertIn("Entries requiring review", report)
        self.assertIn("HOL Guard candidates", report)
        self.assertIn("no surface", report)


if __name__ == "__main__":
    unittest.main()
