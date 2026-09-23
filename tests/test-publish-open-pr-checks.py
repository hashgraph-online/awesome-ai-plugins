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
    def test_failed_source_scan_is_advisory(self):
        result = {"pr_number": 411, "state": "scan", "failure_reasons": []}
        conclusion, title, summary = MODULE.check_summary(
            result, {411: ["failure"]}
        )

        self.assertEqual(conclusion, "success")
        self.assertEqual(title, "scan findings")
        self.assertIn("advisory for this catalog", summary)
        self.assertIn("does not block the PR", summary)
        self.assertNotIn("80", summary)

    def test_missing_source_scanner_ci_is_optional(self):
        guidance = MODULE.optional_scanner_ci_guidance(["owner/plugin"])

        self.assertIn("Recommended: add scanner CI for security", guidance)
        self.assertIn("This listing can merge without it", guidance)
        self.assertIn("improves the HOL Registry trust score", guidance)
        self.assertIn("trust badge", guidance)
        self.assertIn("10% trust-score reduction", guidance)
        self.assertIn("full trust score", guidance)

    def test_success_comment_keeps_required_state_separate_from_scan_advice(self):
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
            "success",
            "scan findings",
            "The centralized source scan returned: failure. Source scanning is advisory for this catalog, so this does not block the PR.",
            "https://github.com/example/repo/actions/runs/1",
        )

        self.assertIn("Contribution check passed", body)
        self.assertIn("@builder, the required catalog checks passed.", body)
        self.assertIn("Recommended: add scanner CI for security", body)
        self.assertIn("This listing can merge without it", body)
        self.assertIn("trust badge", body)
        self.assertIn("10% trust-score reduction", body)
        self.assertIn("full trust score", body)
        self.assertNotIn("listing threshold", body)


if __name__ == "__main__":
    unittest.main()
