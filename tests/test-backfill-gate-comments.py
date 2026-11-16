import importlib.util
import io
import json
import os
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.request import Request

SPEC = importlib.util.spec_from_file_location(
    "backfill_gate_comments",
    Path(__file__).resolve().parents[1] / "scripts/backfill-gate-comments.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

REPOSITORY = "owner/catalog"
LEGACY_BODY = ("@builder The contribution gate was met. "
               f"[Sweep run](https://github.com/{REPOSITORY}/actions/runs/77/attempts/2)\n\n"
               + MODULE.PUBLISH.COMMENT_MARKER)


def bot_comment(body, login="github-actions[bot]"):
    return {"id": 9, "user": {"login": login}, "body": body}


def closed_pull(merged=True):
    return {"number": 411, "title": "Add plugin", "state": "closed", "merged": merged,
            "user": {"login": "builder"}, "head": {"sha": "b" * 40}, "base": {"sha": "c" * 40}}


def artifact_zip(report):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("findings.json", json.dumps(report))
    return buffer.getvalue()


class BackfillGateCommentTests(unittest.TestCase):
    def setUp(self):
        self.api = patch.object(MODULE.PUBLISH, "github_api").start()
        self.comments = patch.object(MODULE.PUBLISH, "list_issue_comments").start()
        self.addCleanup(patch.stopall)

    def patched_bodies(self):
        return [call.kwargs["payload"]["body"] for call in self.api.call_args_list if call.kwargs.get("method") == "PATCH"]

    def test_pr_list_is_strict_and_bounded(self):
        self.assertEqual(MODULE.parse_pr_numbers(" 90, 247 90\n1123 "), [90, 247, 1123])
        for raw in ("", "90;rm", "-1", "0", " ".join(str(n) for n in range(1, 102))):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                MODULE.parse_pr_numbers(raw)

    def test_only_legacy_bot_comments_on_closed_prs_are_edited(self):
        cases = [
            ([], {}, "no gate comment"),
            ([bot_comment(LEGACY_BODY, login="someone")], {}, "no gate comment"),
            ([bot_comment("## ✅ Plugin scan passed\n" + MODULE.PUBLISH.COMMENT_MARKER)], {}, "already current"),
            ([bot_comment(LEGACY_BODY)], {**closed_pull(), "state": "open"}, "refreshed by the sweep"),
        ]
        for comments, pull, expected in cases:
            with self.subTest(expected=expected):
                self.comments.return_value = comments
                self.api.return_value = pull
                self.assertIn(expected, MODULE.backfill_pull_request(REPOSITORY, "token", 411, "https://run"))
        self.assertEqual(self.patched_bodies(), [])

    def test_merged_pr_is_rerendered_with_retained_report(self):
        self.comments.return_value = [bot_comment(LEGACY_BODY)]
        report = {"schema_version": "sanitized-scan.v1", "workflow_run_id": "55", "workflow_run_attempt": 1,
                  "source_commit": "a" * 40, "score": 91, "scanner_version": "3.58.0", "categories": [], "findings": []}
        artifacts = {"artifacts": [{"id": 3, "expired": False, "created_at": "2026-10-30T00:00:00Z",
                                    "workflow_run": {"id": 55}}]}

        def api(repository, path, token, **kwargs):
            if path == "/pulls/411":
                return closed_pull()
            if path.startswith("/actions/artifacts?"):
                return artifacts if "attempt-1" in path else {"artifacts": []}
            return {}

        self.api.side_effect = api
        result = {"pr_number": 411, "title": "Add plugin", "head_sha": "b" * 40, "author_login": "builder",
                  "pr_state": "merged", "state": "scan", "failure_reasons": [],
                  "contributions": [{"owner": "owner", "repo": "plugin", "scanner_ci": "not_detected",
                                     "default_branch": "main"}]}
        with patch.object(MODULE, "pull_request_result", return_value=result), \
                patch.object(MODULE, "download_report", return_value=report):
            outcome = MODULE.backfill_pull_request(REPOSITORY, "token", 411, "https://run")

        self.assertIn("updated (merged, scan, 1 scored", outcome)
        [body] = self.patched_bodies()
        self.assertTrue(body.startswith("## ✅ Plugin scan passed"))
        self.assertIn("This listing has been merged", body)
        self.assertIn("| **91** | 75 |", body)
        self.assertIn(f"https://github.com/{REPOSITORY}/actions/runs/55/attempts/1", body)
        self.assertIn("@builder", body)

    def test_merged_pr_without_report_keeps_legacy_run_and_passes(self):
        self.comments.return_value = [bot_comment(LEGACY_BODY)]
        self.api.side_effect = lambda repository, path, token, **kwargs: (
            closed_pull() if path == "/pulls/411" else {"artifacts": []})
        result = {"pr_number": 411, "head_sha": "b" * 40, "author_login": "builder", "pr_state": "merged",
                  "state": "scan", "failure_reasons": [],
                  "contributions": [{"owner": "owner", "repo": "plugin", "scanner_ci": "maintained"}]}
        with patch.object(MODULE, "pull_request_result", return_value=result):
            MODULE.backfill_pull_request(REPOSITORY, "token", 411, "https://run")

        [body] = self.patched_bodies()
        self.assertTrue(body.startswith("## ✅ Plugin scan passed"))
        self.assertIn(f"https://github.com/{REPOSITORY}/actions/runs/77/attempts/2", body)

    def test_artifact_download_never_forwards_the_token_off_github(self):
        handler = MODULE.StripAuthOnRedirect()
        request = Request("https://api.github.com/x", headers={"Authorization": "Bearer secret"})
        redirected = handler.redirect_request(request, None, 302, "Found", {}, "https://storage.example/blob")
        self.assertIsNone(redirected.get_header("Authorization"))

    def test_artifact_report_is_read_from_a_bounded_zip(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = artifact_zip({"score": 80})
        opener = MagicMock()
        opener.open.return_value = response
        with patch.object(MODULE, "build_opener", return_value=opener):
            self.assertEqual(MODULE.download_report(REPOSITORY, "token", 3), {"score": 80})
        response.__enter__.return_value.read.return_value = b"not a zip"
        with patch.object(MODULE, "build_opener", return_value=opener):
            self.assertIsNone(MODULE.download_report(REPOSITORY, "token", 3))

    def test_main_reports_failures_without_stopping(self):
        env = {"GITHUB_REPOSITORY": REPOSITORY, "GITHUB_TOKEN": "token", "BACKFILL_RUN_URL": "https://run",
               "BACKFILL_PRS": "1 2"}
        with patch.dict(os.environ, env), patch.object(
                MODULE, "backfill_pull_request", side_effect=[RuntimeError("boom"), "updated"]) as backfill:
            self.assertEqual(MODULE.main(), 1)
        self.assertEqual(backfill.call_count, 2)


if __name__ == "__main__":
    unittest.main()
