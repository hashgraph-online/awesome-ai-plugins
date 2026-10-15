import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

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
        self.assertEqual(title, "Centralized score not established")
        self.assertIn("score of at least 80", summary)
        self.assertIn("Finding severities are advisory", summary)
        self.assertIn("Keep the contribution blocked", summary)

    def test_unavailable_source_scan_blocks_merge(self):
        conclusion, title, summary = MODULE.check_summary(
            {"pr_number": 411, "state": "scan", "failure_reasons": []}, {}
        )

        self.assertEqual(conclusion, "failure")
        self.assertEqual(title, "Centralized score unavailable")
        self.assertIn("did not establish a score of at least 80", summary)

    def test_failed_scan_comment_is_one_paragraph_with_conditional_source_ci_guidance(self):
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
            "Centralized score not established",
            "The centralized scan did not establish a score of at least 80.",
            "https://github.com/example/repo/actions/runs/1",
        )

        self.assertNotIn("\n", body)
        self.assertNotIn("\\n", body)
        self.assertIn("@builder, The centralized scan did not establish", body)
        self.assertIn("source-repository scanner CI is recommended, not required", body)
        self.assertIn("If the completed report confirms a score below 80", body)
        self.assertIn("reproduce locally", body)
        self.assertIn("link the source run", body)
        self.assertIn("request review", body)
        self.assertNotIn("Community Plugins format", body)

    def test_passing_score_does_not_recommend_source_scanner_ci(self):
        result = {
            "pr_number": 411,
            "state": "scan",
            "author_login": "builder",
            "contributions": [{"owner": "owner", "repo": "plugin", "scanner_ci": "not_detected"}],
        }
        body = MODULE.remediation_comment(
            result,
            "success",
            "Centralized score threshold passed",
            "The centralized scan met the score threshold of 80.",
            "https://github.com/example/repo/actions/runs/1",
        )

        self.assertNotIn("\n", body)
        self.assertNotIn("\\n", body)
        self.assertIn("score threshold of 80 was met", body)
        self.assertNotIn("scanner CI is recommended", body)
        self.assertNotIn("trust score", body)

    def test_catalog_failure_comment_is_one_paragraph_without_scanner_gate(self):
        result = {
            "pr_number": 411,
            "state": "failure",
            "author_login": "builder",
            "failure_reasons": ["entry is not alphabetical"],
        }
        body = MODULE.remediation_comment(
            result,
            "failure",
            "Contribution requirements failed",
            "Catalog validation failed: entry is not alphabetical",
            "https://github.com/example/repo/actions/runs/1",
        )

        self.assertNotIn("\n", body)
        self.assertIn("entry is not alphabetical", body)
        self.assertNotIn("scanner CI", body)

    def test_failed_scan_check_links_sanitized_artifact_for_new_title(self):
        published = []

        def fake_github_api(_repository, path, _token, *, method="GET", payload=None):
            if path.startswith("/commits/"):
                return {"check_runs": []}
            if path == "/check-runs":
                published.append(payload)
                return {}
            return {}

        with patch.object(MODULE, "github_api", side_effect=fake_github_api), patch.object(
            MODULE, "upsert_remediation_comment"
        ):
            MODULE.publish_check(
                "example/repo",
                {
                    "pr_number": 411,
                    "head_sha": "a" * 40,
                    "title": "Add example plugin",
                    "state": "scan",
                    "failure_reasons": [],
                },
                {411: ["failure"]},
                "https://github.com/example/repo/actions/runs/1",
                "token",
            )

        self.assertEqual(len(published), 1)
        summary = published[0]["output"]["summary"]
        self.assertIn("download sanitized JSON/SARIF findings", summary)
        self.assertIn("scan-findings-pr-411-", summary)


if __name__ == "__main__":
    unittest.main()
