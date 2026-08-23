from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts/validate-contribution.py"
SPEC = importlib.util.spec_from_file_location("validate_contribution", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

Contribution = MODULE.Contribution


def contribution(repo: str = "example-plugin") -> Contribution:
    return Contribution(
        display_name="Example Plugin",
        url=f"https://github.com/example/{repo}",
        owner="example",
        repo=repo,
        description="An example plugin.",
    )


MAINTAINED_WORKFLOW = """
name: Scan
on:
  push:
  pull_request:
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: hashgraph-online/ai-plugin-scanner-action@abc123
"""

UNRELATED_WORKFLOW = """
name: CI
on:
  push:
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
"""


class ValidateContributionTests(unittest.TestCase):
    def test_repository_without_scanner_ci_is_valid_and_enters_scan_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            matrix_path = Path(directory) / "matrix.json"
            with patch.object(MODULE, "workflow_files", return_value=[]):
                inspection = MODULE.inspect_scanner_ci(contribution())
                self.assertEqual(inspection.status, "not_detected")
                MODULE.write_matrix(matrix_path, [contribution()])
            payload = json.loads(matrix_path.read_text(encoding="utf-8"))
            self.assertEqual(
                payload,
                [{"owner": "example", "repo": "example-plugin"}],
            )

    def test_repository_with_scanner_ci_is_marked_maintained(self) -> None:
        with patch.object(
            MODULE,
            "workflow_files",
            return_value=[("scan.yml", MAINTAINED_WORKFLOW)],
        ):
            inspection = MODULE.inspect_scanner_ci(contribution())
        self.assertEqual(inspection.status, "maintained")
        self.assertEqual(inspection.workflow_paths, ("scan.yml",))

    def test_unreadable_workflows_are_unknown(self) -> None:
        with patch.object(
            MODULE,
            "workflow_files",
            return_value=[("scan.yml", ":\n- :")],
        ), patch.object(
            MODULE,
            "parse_workflow_document",
            side_effect=MODULE.ValidationError("invalid yaml"),
        ):
            inspection = MODULE.inspect_scanner_ci(contribution())
        self.assertEqual(inspection.status, "unknown")

    def test_missing_source_repository_fails_catalog_validation(self) -> None:
        with patch.object(
            MODULE,
            "github_json",
            side_effect=MODULE.ValidationError("source repository or workflow directory was not found"),
        ):
            with self.assertRaisesRegex(MODULE.ValidationError, "reachable public GitHub repository"):
                MODULE.ensure_public_github_repository(contribution("missing-plugin"))

    def test_malformed_readme_change_still_fails(self) -> None:
        head = (
            "## Community Plugins\n"
            "- [Broken Plugin](https://example.com/not-github) - not a github repo\n"
        )
        diff = (
            "@@ -1,1 +1,2 @@\n"
            " ## Community Plugins\n"
            "+- [Broken Plugin](https://example.com/not-github) - not a github repo\n"
        )
        malformed = MODULE.malformed_community_plugin_lines(diff, head)
        self.assertEqual(len(malformed), 1)


class PublishOpenPrChecksTests(unittest.TestCase):
    def setUp(self) -> None:
        publisher_path = (
            Path(__file__).resolve().parents[1] / "scripts/publish-open-pr-checks.py"
        )
        spec = importlib.util.spec_from_file_location("publish_open_pr_checks", publisher_path)
        assert spec and spec.loader
        self.publisher = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.publisher
        spec.loader.exec_module(self.publisher)

    def test_check_succeeds_with_missing_ci_and_includes_optional_guidance(self) -> None:
        result = {
            "pr_number": 12,
            "state": "scan",
            "title": "Add example plugin",
            "author_login": "octocat",
        }
        conclusion, title, summary = self.publisher.check_summary(result, {12: []})
        self.assertEqual(conclusion, "success")
        self.assertEqual(title, "scan unavailable")
        self.assertIn("advisory", summary.lower())
        comment = self.publisher.remediation_comment(
            result,
            conclusion,
            title,
            summary,
            "https://example.test/run",
        )
        self.assertIn("Contribution check passed", comment)
        self.assertNotIn("needs updates before it can be merged", comment)
        self.assertNotIn("must invoke", comment)

    def test_check_succeeds_when_advisory_scan_fails(self) -> None:
        result = {
            "pr_number": 12,
            "state": "scan",
            "title": "Add example plugin",
            "author_login": "octocat",
        }
        conclusion, title, summary = self.publisher.check_summary(
            result,
            {12: ["failure"]},
        )
        self.assertEqual(conclusion, "success")
        self.assertEqual(title, "scan findings")
        self.assertIn("advisory", summary.lower())
        comment = self.publisher.remediation_comment(
            result,
            conclusion,
            title,
            summary,
            "https://example.test/run",
        )
        self.assertNotIn("needs updates before it can be merged", comment)

    def test_scan_passed_title_is_used_when_jobs_succeed(self) -> None:
        conclusion, title, _summary = self.publisher.check_summary(
            {
                "pr_number": 12,
                "state": "scan",
                "title": "Add example plugin",
            },
            {12: ["success", "success"]},
        )
        self.assertEqual(conclusion, "success")
        self.assertEqual(title, "scan passed")

    def test_comment_updater_replaces_the_prior_marker_comment(self) -> None:
        calls: list[tuple[str, str]] = []

        def fake_github_api(_repository: str, path: str, _token: str, **kwargs: object) -> object:
            calls.append((str(kwargs.get("method", "GET")), path))
            if path.startswith("/issues/") and path.endswith("/comments"):
                return {"id": 99}
            return {}

        result = {
            "pr_number": 12,
            "state": "scan",
            "title": "Add example plugin",
            "author_login": "octocat",
        }
        with patch.object(self.publisher, "github_api", side_effect=fake_github_api), patch.object(
            self.publisher,
            "list_issue_comments",
            return_value=[
                {
                    "id": 44,
                    "body": "<!-- awesome-ai-plugins-contribution-gate -->\nRequired scanner CI is missing.",
                }
            ],
        ):
            self.publisher.upsert_remediation_comment(
                "hashgraph-online/awesome-ai-plugins",
                result,
                "success",
                "scan unavailable",
                "HOL centralized scan could not run.",
                "https://example.test/run",
                "token",
            )
        self.assertEqual(calls, [("PATCH", "/issues/comments/44")])
        self.assertNotIn(("POST", "/issues/12/comments"), calls)


if __name__ == "__main__":
    unittest.main()
