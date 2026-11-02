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

    def test_artifact_publisher_renders_and_publishes_measured_and_missing_scores(self):
        for score, expected in ((75, "success"), (74, "failure"), (None, "failure")):
            with self.subTest(score=score), tempfile.TemporaryDirectory() as directory:
                results = self.artifact_fixture(directory, score=score)
                results[0].update(head_sha="b" * 40, title="Add example plugin", failure_reasons=[])
                env = {"GITHUB_REPOSITORY": "example/repo", "GITHUB_TOKEN": "test-token",
                       "GITHUB_RUN_ID": "1", "GITHUB_RUN_ATTEMPT": "2",
                       "SWEEP_RUN_URL": "https://github.com/example/repo/actions/runs/1",
                       "OPEN_PR_RESULTS": json.dumps(results), "SCAN_FINDINGS_DIR": directory}
                with patch.dict(MODULE.os.environ, env, clear=True), patch.object(
                    MODULE, "github_api", return_value={"check_runs": []}
                ) as api, patch.object(MODULE, "upsert_remediation_comment"):
                    self.assertEqual(MODULE.main(), 0)
                payloads = [call.kwargs["payload"] for call in api.call_args_list
                            if call.args[1] == "/check-runs"]
                self.assertEqual(len(payloads), 1)
                self.assertEqual(payloads[0]["conclusion"], expected)
                self.assertIn("owner/plugin", payloads[0]["output"]["summary"])
                if score is None:
                    self.assertIn("No score produced", payloads[0]["output"]["summary"])
                else:
                    self.assertIn(f"**{score}**", payloads[0]["output"]["summary"])

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

    def scan_result(self, scanner_ci="not_detected", branch="main"):
        return {
            "pr_number": 411,
            "state": "scan",
            "author_login": "builder",
            "contributions": [
                {"owner": "owner", "repo": "plugin", "scanner_ci": scanner_ci, "default_branch": branch}
            ],
        }

    def scan_report(self, score=63, **changes):
        report = {
            "source_commit": "a" * 40,
            "score": score,
            "scanner_version": "3.58.0",
            "categories": [
                {"name": "Security", "score": 10, "max": 20,
                 "failed_checks": [{"name": "No hardcoded secrets", "points": 0, "max": 10}]},
                {"name": "Best Practices", "score": 10, "max": 10, "failed_checks": []},
            ],
            "findings": [
                {"ruleId": "HARDCODED_SECRET", "severity": "high", "filePath": "src/config.py",
                 "lineNumber": 12, "remediation": "Move the secret to an environment variable."},
                {"ruleId": "HARDCODED_SECRET", "severity": "high", "filePath": "src/config.py",
                 "lineNumber": 12, "remediation": "Move the secret to an environment variable."},
                {"ruleId": "LICENSE_MISSING", "severity": "low", "filePath": "LICENSE",
                 "lineNumber": None, "remediation": "Add a LICENSE file."},
            ],
        }
        report.update(changes)
        return {"owner": "owner", "repo": "plugin", "report": report}

    def render(self, result, conclusion, reports):
        return MODULE.remediation_comment(
            result,
            conclusion,
            "title",
            "summary",
            "https://github.com/example/repo/actions/runs/1",
            reports=reports,
        )

    def test_failed_scan_comment_shows_score_gap_and_tables(self):
        body = self.render(self.scan_result(), "failure", [self.scan_report()])

        self.assertTrue(body.startswith("## ❌ Plugin scan: 63 / 100 (needs 75)"))
        self.assertTrue(body.endswith("\n" + MODULE.COMMENT_MARKER))
        self.assertIn("@builder", body)
        self.assertIn("12 more points", body)
        self.assertIn("| Source repository |", body)
        self.assertIn("| Severity | Rule | Hits | How to fix |", body)
        self.assertIn("`HARDCODED_SECRET`", body)
        self.assertIn("blob/" + "a" * 40 + "/src/config.py#L12", body)
        self.assertIn("×2", body)
        self.assertIn("(add this file)", body)
        self.assertNotIn("blob/" + "a" * 40 + "/LICENSE", body)
        self.assertIn("No hardcoded secrets", body)
        self.assertIn("[!WARNING]", body)
        self.assertIn("git commit --allow-empty", body)
        self.assertIn("scan-findings-pr-411-", body)

    def test_missing_scanner_ci_offers_pinned_one_click_install(self):
        body = self.render(self.scan_result(), "failure", [self.scan_report()])

        self.assertIn("https://github.com/owner/plugin/new/main?filename=.github/workflows/hol-plugin-scanner.yml&value=", body)
        self.assertIn("hashgraph-online%2Fai-plugin-scanner-action%40", body)
        self.assertIn("pipx run --spec plugin-scanner==3.58.0 plugin-scanner scan . --min-score 75", body)
        self.assertIn("optional", body)
        template = MODULE.workflow_template()
        self.assertIsNotNone(template)
        self.assertRegex(template, r"ai-plugin-scanner-action@[0-9a-f]{40} # v")
        self.assertIn("contents: read", template)
        self.assertIn("persist-credentials: false", template)

    def test_invalid_branch_suppresses_install_link(self):
        for branch in (None, "../main", "-x", "main?x=1"):
            with self.subTest(branch=branch):
                body = self.render(self.scan_result(branch=branch), "failure", [self.scan_report()])
                self.assertNotIn("/new/", body)

    def test_maintained_scanner_ci_is_acknowledged_without_install_link(self):
        body = self.render(self.scan_result(scanner_ci="maintained"), "failure", [self.scan_report()])

        self.assertIn("Scanner CI detected", body)
        self.assertNotIn("/new/", body)

    def test_passing_scan_comment_still_offers_scanner_ci(self):
        body = self.render(self.scan_result(), "success", [self.scan_report(score=88, findings=[])])

        self.assertTrue(body.startswith("## ✅ Plugin scan passed"))
        self.assertIn("| **88** | 75 | ✅ Passes |", body)
        self.assertNotIn("git commit --allow-empty", body)
        self.assertIn("/new/main?", body)
        self.assertTrue(body.endswith("\n" + MODULE.COMMENT_MARKER))

    def test_merged_and_closed_comments_do_not_ask_for_a_push(self):
        merged = self.render({**self.scan_result(), "pr_state": "merged"}, "success",
                             [self.scan_report(score=88, findings=[])])
        self.assertIn("This listing has been merged; no action is needed.", merged)
        self.assertNotIn("a maintainer will review", merged)

        closed = self.render({**self.scan_result(), "pr_state": "closed"}, "failure", [self.scan_report()])
        self.assertIn("open a new pull request", closed)
        self.assertNotIn("git commit --allow-empty", closed)
        unscored = self.render({**self.scan_result(), "pr_state": "closed"}, "failure", None)
        self.assertNotIn("push to this pull request", unscored)

    def test_no_contributions_never_claims_scanner_ci(self):
        self.assertEqual(MODULE.scanner_ci_section(self.scan_result(), [], True), [])

    def test_missing_report_does_not_claim_a_score(self):
        body = self.render(self.scan_result(), "failure", None)

        self.assertTrue(body.startswith("## ⚠️ Plugin scan did not produce a score"))
        self.assertNotIn("needs 75)", body.splitlines()[0])

    def test_untrusted_artifact_text_cannot_inject_markdown(self):
        report = self.scan_report(findings=[
            {"ruleId": "RULE_X", "severity": "high", "filePath": "a.py", "lineNumber": 1,
             "remediation": "see [here](https://evil.example) <img src=x>"},
            {"ruleId": "bad](x)", "severity": "high", "filePath": "b.py", "lineNumber": 1, "remediation": "x"},
            {"ruleId": "RULE_Y", "severity": "high", "filePath": "@evil/x", "lineNumber": 1, "remediation": "x"},
        ], categories=[{"name": "<script>", "score": 1, "max": 2, "failed_checks": []}])
        body = self.render(self.scan_result(), "failure", [report])

        self.assertNotIn("evil", body)
        self.assertNotIn("<img", body)
        self.assertNotIn("<script>", body)
        self.assertNotIn("bad](x)", body)
        self.assertIn(MODULE.GENERIC_REMEDIATION, body)

    def test_catalog_failure_comment_lists_reasons_without_scanner_section(self):
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

        self.assertTrue(body.startswith("## ❌ Contribution checks failed"))
        self.assertTrue(body.endswith("\n" + MODULE.COMMENT_MARKER))
        self.assertIn("- entry is not alphabetical", body)
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


    def test_check_run_renders_report_tables_and_score_title(self):
        published = []

        def fake_github_api(_repository, path, _token, *, method="GET", payload=None):
            if path.startswith("/commits/"):
                return {"check_runs": []}
            if path == "/check-runs":
                published.append(payload)
            return {}

        result = self.scan_result()
        result.update({"head_sha": "a" * 40, "title": "Add example plugin", "failure_reasons": []})
        with patch.object(MODULE, "github_api", side_effect=fake_github_api), patch.object(
            MODULE, "upsert_remediation_comment"
        ) as upsert:
            MODULE.publish_check(
                "example/repo",
                result,
                {411: ["failure"]},
                "https://github.com/example/repo/actions/runs/1",
                "token",
                reports={411: [self.scan_report()]},
            )

        output = published[0]["output"]
        self.assertEqual(output["title"], "Scan score 63 / 100 (needs 75)")
        self.assertIn("| Severity | Rule | Hits | How to fix |", output["summary"])
        self.assertNotIn(MODULE.COMMENT_MARKER, output["summary"])
        upsert.assert_called_once()

if __name__ == "__main__":
    unittest.main()
