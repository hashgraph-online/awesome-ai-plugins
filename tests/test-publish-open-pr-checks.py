import importlib.util
import json
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
    def test_centralized_scans_use_the_inclusive_75_point_minimum(self):
        root = Path(__file__).resolve().parents[1]
        for workflow in ("sweep-open-prs.yml", "validate-contribution.yml"):
            with self.subTest(workflow=workflow):
                text = (root / ".github/workflows" / workflow).read_text()
                self.assertIn("min_score: 75", text)
                self.assertIn("fail_on_severity: none", text)
                self.assertNotIn("min_score: 80", text)

    def test_publisher_waits_for_completed_scan_before_reporting(self):
        result = {"pr_number": 411, "state": "scan", "contributions": [{"owner": "owner", "repo": "plugin"}]}
        env = {
            "GITHUB_REPOSITORY": "example/repo",
            "GITHUB_TOKEN": "test-token",
            "GITHUB_RUN_ID": "1",
            "SWEEP_RUN_URL": "https://github.com/example/repo/actions/runs/1",
            "OPEN_PR_RESULTS": json.dumps([result]),
        }
        responses = [
            {"jobs": []},
            {"jobs": [{"id": 7, "run_attempt": 1, "name": "Scan PR (411, owner, plugin)", "conclusion": "success"}]},
        ]
        with patch.dict(MODULE.os.environ, env, clear=True), patch.object(MODULE, "github_api", side_effect=responses), patch.object(MODULE, "publish_check") as publish, patch("time.sleep"):
            self.assertEqual(MODULE.main(), 0)
        self.assertEqual(publish.call_args.args[2], {411: ["success"]})

    def test_waits_for_scanner_job_to_appear(self):
        responses = [
            {"jobs": []},
            {"jobs": [{"id": 7, "run_attempt": 1, "name": "Scan PR (411, owner, plugin)", "conclusion": "success"}]},
        ]
        with patch.object(MODULE, "github_api", side_effect=responses) as api, patch.object(MODULE.time, "sleep") as sleep:
            result = MODULE.scan_conclusions("example/repo", "1", "token", {411: 1})
        self.assertEqual(result, {411: ["success"]})
        self.assertEqual(api.call_count, 2)
        self.assertIn("filter=all", api.call_args_list[0].args[1])
        sleep.assert_called_once()

    def test_latest_scanner_attempt_replaces_previous_failure(self):
        jobs = [
            {"id": 1, "run_attempt": 1, "name": "Scan PR (411, owner, plugin)", "conclusion": "failure"},
            {"id": 2, "run_attempt": 2, "name": "Scan PR (411, owner, plugin)", "conclusion": "success"},
        ]
        with patch.object(MODULE, "github_api", return_value={"jobs": jobs}):
            result = MODULE.scan_conclusions("example/repo", "1", "token", {411: 1})
        self.assertEqual(result, {411: ["success"]})

    def test_partial_or_pending_matrix_never_passes(self):
        jobs = [
            {"id": 1, "run_attempt": 1, "name": "Scan PR (411, owner, first)", "conclusion": "success"},
            {"id": 2, "run_attempt": 1, "name": "Scan PR (411, owner, second)", "conclusion": "success"},
            {"id": 3, "run_attempt": 2, "name": "Scan PR (411, owner, second)", "conclusion": None},
        ]
        with patch.object(MODULE, "github_api", return_value={"jobs": jobs}), patch.object(MODULE.time, "sleep"), patch.object(MODULE, "SCAN_LOOKUP_ATTEMPTS", 2):
            result = MODULE.scan_conclusions("example/repo", "1", "token", {411: 2})
        self.assertNotIn(411, result)
        self.assertEqual(MODULE.check_summary({"pr_number": 411, "state": "scan"}, result)[0], "failure")

    def test_missing_matrix_job_never_passes(self):
        jobs = [{"id": 1, "run_attempt": 1, "name": "Scan PR (411, owner, first)", "conclusion": "success"}]
        with patch.object(MODULE, "github_api", return_value={"jobs": jobs}), patch.object(MODULE.time, "sleep"), patch.object(MODULE, "SCAN_LOOKUP_ATTEMPTS", 2):
            result = MODULE.scan_conclusions("example/repo", "1", "token", {411: 2})
        self.assertNotIn(411, result)

    def test_previous_attempt_cannot_clear_a_new_attempt(self):
        jobs = [{"id": 1, "run_attempt": 1, "name": "Scan PR (411, owner, plugin)", "conclusion": "success"}]
        with patch.object(MODULE, "github_api", return_value={"jobs": jobs}), patch.object(MODULE.time, "sleep"), patch.object(MODULE, "SCAN_LOOKUP_ATTEMPTS", 2):
            result = MODULE.scan_conclusions("example/repo", "1", "token", {411: 1}, 2)
        self.assertNotIn(411, result)

    def test_failed_source_scan_blocks_merge(self):
        result = {"pr_number": 411, "state": "scan", "failure_reasons": []}
        conclusion, title, summary = MODULE.check_summary(
            result, {411: ["failure"]}
        )

        self.assertEqual(conclusion, "failure")
        self.assertEqual(title, "Centralized scan did not pass")
        self.assertIn("score of at least 75", summary)
        self.assertIn("job conclusion alone cannot distinguish", summary)
        self.assertIn("original scan job log and sanitized findings", summary)
        self.assertIn("Finding severities are advisory", summary)
        self.assertIn("Keep the contribution blocked", summary)

    def test_cancelled_source_scan_does_not_claim_no_numeric_score(self):
        conclusion, title, summary = MODULE.check_summary(
            {"pr_number": 411, "state": "scan", "failure_reasons": []},
            {411: ["cancelled"]},
        )
        self.assertEqual(conclusion, "failure")
        self.assertEqual(title, "Centralized scan did not pass")
        self.assertIn("ended with cancelled", summary)
        self.assertIn("cannot distinguish", summary)

    def test_unavailable_source_scan_blocks_merge(self):
        conclusion, title, summary = MODULE.check_summary(
            {"pr_number": 411, "state": "scan", "failure_reasons": []}, {}
        )

        self.assertEqual(conclusion, "failure")
        self.assertEqual(title, "Centralized score unavailable")
        self.assertIn("did not establish a score of at least 75", summary)

    def test_failed_scan_comment_uses_sections_with_conditional_source_ci_guidance(self):
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
            "The centralized scan did not establish a score of at least 75.",
            "https://github.com/example/repo/actions/runs/1",
        )

        self.assertIn("\n", body)
        self.assertNotIn("\\n", body)
        self.assertIn("@builder, the centralized scan did not establish", body)
        self.assertIn("### Merge status", body)
        self.assertIn("### Findings", body)
        self.assertIn("scan-findings-pr-411-", body)
        self.assertIn("### References", body)
        self.assertIn("source-repository scanner CI is recommended, not required", body)
        self.assertIn("If the completed report confirms a score below 75", body)
        self.assertIn("Reproduce locally", body)
        self.assertIn("Link the source run", body)
        self.assertIn("request review", body)
        self.assertNotIn("SCANNER_GUIDE.md, reproduce locally", body)
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
            "The centralized scan met the score threshold of 75.",
            "https://github.com/example/repo/actions/runs/1",
        )

        self.assertNotIn("\n", body)
        self.assertNotIn("\\n", body)
        self.assertIn("score threshold of 75 was met", body)
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
