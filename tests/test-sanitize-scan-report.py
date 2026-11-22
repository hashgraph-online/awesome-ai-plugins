import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("sanitize_report", Path(__file__).parents[1] / "scripts/sanitize-scan-report.py")
reporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reporter)


class ScanReportTests(unittest.TestCase):
    def test_workflow_identity_is_allowlisted_and_cannot_come_from_source_report(self):
        raw = {"score": 75, "findings": [], "workflow_run_id": "999", "workflow_run_attempt": 99}
        report = reporter.sanitize(raw, Path("/scan"), "a" * 40, "123", "2")
        self.assertEqual(report["workflow_run_id"], "123")
        self.assertEqual(report["workflow_run_attempt"], 2)
        report = reporter.sanitize(raw, Path("/scan"), "a" * 40, "private", "-1")
        self.assertIsNone(report["workflow_run_id"])
        self.assertIsNone(report["workflow_run_attempt"])

    def test_allowlist_preserves_location_but_never_source_or_secret_text(self):
        raw = {"score": 75, "pluginDir": "/outside/private", "findings": [{
            "ruleId": "HARDCODED_SECRET", "severity": "high", "filePath": "/scan/tests/fixture.py",
            "lineNumber": 12, "description": "token=private", "source": "private", "remediation": "private"}]}
        report = reporter.sanitize(raw, Path("/scan"), "a" * 40)
        self.assertEqual("tests/fixture.py", report["findings"][0]["filePath"])
        self.assertEqual(12, report["findings"][0]["lineNumber"])
        self.assertNotIn("private", json.dumps(report))
        self.assertNotIn("private", json.dumps(reporter.sarif(report)))

    def test_category_breakdown_and_version_keep_only_allowlisted_fields(self):
        raw = {"score": 63, "tool_version": "3.58.0", "findings": [], "categories": [
            {"name": "Security", "score": 10, "max": 20, "checks": [
                {"name": "No hardcoded secrets", "passed": False, "points": 0, "maxPoints": 10,
                 "message": "token=private", "findings": [{"source": "private"}]},
                {"name": "Pinned actions", "passed": True, "points": 10, "maxPoints": 10}]},
            {"name": "<script>", "score": 1, "max": 2, "checks": []},
            {"name": "Metadata", "score": True, "max": 5, "checks": []}]}
        report = reporter.sanitize(raw, Path("/scan"), "a" * 40)
        self.assertEqual("3.58.0", report["scanner_version"])
        self.assertEqual([{"name": "Security", "score": 10, "max": 20, "failed_checks": [
            {"name": "No hardcoded secrets", "points": 0, "max": 10}]}], report["categories"])
        self.assertNotIn("private", json.dumps(report))
        report = reporter.sanitize({"score": 63, "tool_version": "3.58.0`; rm", "categories": "x"}, Path("/scan"), "a" * 40)
        self.assertIsNone(report["scanner_version"])
        self.assertEqual([], report["categories"])

    def test_known_rules_get_specific_guidance(self):
        report = reporter.sanitize({"score": 63, "findings": [
            {"ruleId": "GITHUB_ACTION_UNPINNED", "severity": "medium"},
            {"ruleId": "LICENSE_MISSING", "severity": "low"}]}, Path("/scan"), "a" * 40)
        self.assertIn("40-character commit SHA", report["findings"][0]["remediation"])
        self.assertIn("LICENSE", report["findings"][1]["remediation"])

    def test_unsafe_paths_and_invalid_rules_are_not_published(self):
        for path in ("/outside/file", "../../file", "bad`name", "github_pat_secret"):
            report = reporter.sanitize({"score": "private", "findings": [{
                "ruleId": "TEST_RULE", "severity": "high", "filePath": path, "lineNumber": -1}]}, Path("/scan"), "private")
            self.assertNotIn("filePath", report["findings"][0])
            self.assertIsNone(report["score"])
            self.assertIsNone(report["source_commit"])
        report = reporter.sanitize({"findings": [{"ruleId": "<script>", "severity": "high"}]}, Path("/scan"), "")
        self.assertEqual([], report["findings"])

    def test_sarif_has_file_location_and_severity_without_excerpts(self):
        report = {"findings": [{"ruleId": "TEST", "severity": "high", "filePath": "tests/a b.py",
                                 "lineNumber": 3, "remediation": "Review finding."}]}
        result = reporter.sarif(report)["runs"][0]["results"][0]
        self.assertEqual("error", result["level"])
        self.assertEqual("tests/a%20b.py", result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"])
