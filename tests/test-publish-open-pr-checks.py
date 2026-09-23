import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "publish_open_pr_checks",
    Path(__file__).resolve().parents[1] / "scripts/publish-open-pr-checks.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PublishOpenPrChecksTests(unittest.TestCase):
    def test_failed_source_scan_blocks_merge(self):
        result = {"pr_number": 411, "state": "scan", "failure_reasons": []}
        conclusion, title, summary = MODULE.check_summary(
            result, {411: ["failure"]}
        )

        self.assertEqual(conclusion, "failure")
        self.assertEqual(title, "source scan failed")
        self.assertIn("score at least 80", summary)
        self.assertIn("no critical or high findings", summary)

    def test_unavailable_source_scan_blocks_merge(self):
        conclusion, title, summary = MODULE.check_summary(
            {"pr_number": 411, "state": "scan", "failure_reasons": []}, {}
        )

        self.assertEqual(conclusion, "failure")
        self.assertEqual(title, "source scan unavailable")
        self.assertIn("Rerun the scan", summary)

    def test_missing_source_scanner_ci_is_optional(self):
        guidance = MODULE.optional_scanner_ci_guidance(["owner/plugin"])

        self.assertIn("Recommended: add scanner CI for security", guidance)
        self.assertIn("This listing can merge without it", guidance)
        self.assertIn("improves the HOL Registry trust score", guidance)
        self.assertIn("trust badge", guidance)
        self.assertIn("10% trust-score reduction", guidance)
        self.assertIn("full trust score", guidance)

    def test_failed_scan_comment_distinguishes_optional_source_ci(self):
        result = {
            "pr_number": 411,
            "state": "scan",
            "author_login": "builder",
            "contributions": [
                {
                    "owner": "owner",
                    "repo": "plugin",
                    "scanner_ci": "not_detected",
                }
            ],
        }
        body = MODULE.remediation_comment(
            result,
            "failure",
            "source scan failed",
            "The centralized source scan returned: failure.",
            "https://github.com/example/repo/actions/runs/1",
        )

        self.assertIn("@builder, the required source scan must pass before merge", body)
        self.assertIn("Scanner CI in the source repository is optional", body)
        self.assertIn("centralized scan must pass", body)
        self.assertNotIn("Community Plugins format", body)


if __name__ == "__main__":
    unittest.main()
