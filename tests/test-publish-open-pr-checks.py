import importlib.util
import json
import tempfile
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
    def artifact_fixture(self, directory, **changes):
        report = {"schema_version": "sanitized-scan.v1", "workflow_run_id": "1",
                  "workflow_run_attempt": 2, "source_commit": "a" * 40, "score": 75}
        report.update(changes)
        path = Path(directory) / "scan-findings-pr-411-owner-plugin-attempt-2" / "findings.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(report))
        return [{"pr_number": 411, "state": "scan", "contributions": [{"owner": "owner", "repo": "plugin"}]}]

    def test_artifact_score_is_inclusive_and_independent_of_jobs_api(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(MODULE, "github_api", side_effect=AssertionError("Jobs API must not be used")):
            for score, expected in ((75, "success"), (100, "success"), (74, "failure")):
                result = self.artifact_fixture(directory, score=score)
                self.assertEqual(MODULE.artifact_conclusions(result, directory, "1", 2), {411: [expected]})

    def test_invalid_or_stale_artifact_cannot_clear_the_gate(self):
        for key, value in (("workflow_run_id", "2"), ("workflow_run_attempt", 1),
                           ("workflow_run_attempt", True), ("schema_version", "unknown"),
                           ("source_commit", ""), ("score", None), ("score", True),
                           ("score", 101), ("score", float("nan"))):
            with self.subTest(key=key, value=value), tempfile.TemporaryDirectory() as directory:
                result = self.artifact_fixture(directory, **{key: value})
                self.assertEqual(MODULE.artifact_conclusions(result, directory, "1", 2), {})

    def test_missing_malformed_or_partial_artifact_matrix_never_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.artifact_fixture(directory)
            result[0]["contributions"].append({"owner": "owner", "repo": "missing"})
            self.assertEqual(MODULE.artifact_conclusions(result, directory, "1", 2), {})
            result[0]["contributions"].pop()
            report = Path(directory) / "scan-findings-pr-411-owner-plugin-attempt-2" / "findings.json"
            report.write_text("invalid")
            self.assertEqual(MODULE.artifact_conclusions(result, directory, "1", 2), {})
            report.unlink()
            self.assertEqual(MODULE.artifact_conclusions(result, directory, "1", 2), {})

    def test_publisher_uses_current_attempt_artifacts_without_polling_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.artifact_fixture(directory)
            env = {"GITHUB_REPOSITORY": "example/repo", "GITHUB_TOKEN": "test-token",
                   "GITHUB_RUN_ID": "1", "GITHUB_RUN_ATTEMPT": "2",
                   "SWEEP_RUN_URL": "https://github.com/example/repo/actions/runs/1",
                   "OPEN_PR_RESULTS": json.dumps(result), "SCAN_FINDINGS_DIR": directory}
            with patch.dict(MODULE.os.environ, env, clear=True), patch.object(
                MODULE, "scan_conclusions", return_value={}
            ) as legacy, patch.object(MODULE, "publish_check") as publish:
                self.assertEqual(MODULE.main(), 0)
            self.assertEqual(publish.call_args.args[2], {411: ["success"]})
            legacy.assert_not_called()

    def test_sweep_downloads_run_attempt_bound_reports_for_the_publisher(self):
        text = (Path(__file__).resolve().parents[1] / ".github/workflows/sweep-open-prs.yml").read_text()
        self.assertIn("actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093", text)
        self.assertIn("-attempt-${{ github.run_attempt }}", text)
        self.assertIn("SCAN_FINDINGS_DIR: ${{ runner.temp }}/scan-findings", text)

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

    def test_completed_job_recovers_a_stale_pending_jobs_list(self):
        name = "Scan PR (411, owner, plugin)"
        jobs = {"jobs": [{"id": 7, "run_attempt": 1, "name": name, "conclusion": None}]}
        detail = {"id": 7, "run_id": 1, "run_attempt": 1, "name": name,
                  "status": "completed", "conclusion": "success"}
        with patch.object(MODULE, "github_api", side_effect=[jobs, detail]) as api:
            result = MODULE.scan_conclusions("example/repo", "1", "token", {411: 1})
        self.assertEqual(result, {411: ["success"]})
        self.assertEqual(api.call_args.args[1], "/actions/jobs/7")

    def test_direct_job_cannot_clear_a_different_run_attempt_or_job(self):
        name = "Scan PR (411, owner, plugin)"
        jobs = {"jobs": [{"id": 7, "run_attempt": 2, "name": name, "conclusion": None}]}
        detail = {"id": 7, "run_id": 1, "run_attempt": 2, "name": name,
                  "status": "completed", "conclusion": "success"}
        for field, value in (("id", 8), ("run_id", 2), ("run_attempt", 1),
                             ("name", "Scan PR (412, owner, other)"),
                             ("status", "in_progress"), ("conclusion", None)):
            with self.subTest(field=field), patch.object(MODULE, "SCAN_LOOKUP_ATTEMPTS", 1), patch.object(
                MODULE, "github_api", side_effect=[jobs, {**detail, field: value}]
            ):
                result = MODULE.scan_conclusions("example/repo", "1", "token", {411: 1}, 2)
                self.assertNotIn(411, result)

    def test_direct_failed_job_remains_a_failure(self):
        name = "Scan PR (411, owner, plugin)"
        jobs = {"jobs": [{"id": 7, "run_attempt": 1, "name": name, "conclusion": None}]}
        detail = {"id": 7, "run_id": 1, "run_attempt": 1, "name": name,
                  "status": "completed", "conclusion": "failure"}
        with patch.object(MODULE, "github_api", side_effect=[jobs, detail]):
            result = MODULE.scan_conclusions("example/repo", "1", "token", {411: 1})
        self.assertEqual(result, {411: ["failure"]})
        self.assertEqual(MODULE.check_summary({"pr_number": 411, "state": "scan"}, result)[0], "failure")

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
            "The centralized scan did not establish a score of at least 75.",
            "https://github.com/example/repo/actions/runs/1",
        )

        self.assertNotIn("\n", body)
        self.assertNotIn("\\n", body)
        self.assertIn("@builder, the centralized scan did not establish", body)
        self.assertNotIn("###", body)
        self.assertIn("sanitized findings", body)
        self.assertIn("scan-findings-pr-411-", body)
        self.assertIn("a failed job alone does not establish a score below 75", body)
        self.assertIn("source-repository scanner CI is recommended, not required", body)
        self.assertIn("If the completed report confirms a score below 75", body)
        self.assertIn("Reproduce locally", body)
        self.assertIn("link the source run", body)
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
