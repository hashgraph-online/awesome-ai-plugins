import importlib.util
import io
import json
import os
import re
import runpy
import unittest
from datetime import datetime, timezone
from pathlib import Path
from subprocess import CompletedProcess
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from unittest.mock import mock_open, patch

SPEC = importlib.util.spec_from_file_location(
    "post_claim_notice", Path(__file__).resolve().parents[1] / "scripts/post-claim-notice.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RegistryRetryTests(unittest.TestCase):
    def setUp(self):
        self.sleep = patch.object(MODULE.time, "sleep").start()
        patch.object(MODULE.random, "uniform", return_value=0).start()
        patch.dict(MODULE.CATALOG_REPO_CACHE, clear=True).start()
        patch.dict(os.environ, {"CLAIM_NOTICE_CACHE_CATALOG": "1"}).start()
        self.addCleanup(patch.stopall)

    def response(self, data):
        return io.BytesIO(json.dumps(data).encode())

    def http_error(self, code, retry_after=None):
        headers = {} if retry_after is None else {"Retry-After": retry_after}
        return HTTPError("https://registry.example/catalog", code, "failure", headers, None)

    def test_page_51_rate_limit_retries_same_cursor_and_finishes_snapshot(self):
        pages = [
            self.response({"items": [{"sourceRepo": f"owner/plugin-{i}"}], "nextCursor": str((i + 1) * 50)})
            for i in range(50)
        ]
        pages.extend([
            self.http_error(429, "7"),
            self.response({"items": [{"sourceRepo": "owner/final"}]}),
        ])
        with patch.object(MODULE.urllib.request, "urlopen", side_effect=pages) as fetch:
            repos = MODULE.fetch_catalog_repos()
        self.assertEqual(len(repos), 51)
        self.assertIn("owner/final", repos)
        self.assertEqual(fetch.call_count, 52)
        self.assertEqual(fetch.call_args_list[-2].args[0].full_url, fetch.call_args.args[0].full_url)
        self.assertIn("cursor=2500", fetch.call_args.args[0].full_url)
        self.sleep.assert_called_once_with(7)

    def test_transient_failures_exhaust_bounded_exponential_retries(self):
        for failure in (429, 500, 502, 503, 504, "network", "timeout"):
            with self.subTest(failure=failure):
                self.sleep.reset_mock()
                errors = [
                    URLError("unavailable") if failure == "network" else
                    TimeoutError("timed out") if failure == "timeout" else
                    self.http_error(failure)
                    for _ in range(MODULE.REGISTRY_MAX_ATTEMPTS)
                ]
                with patch.object(MODULE.urllib.request, "urlopen", side_effect=errors) as fetch:
                    with self.assertRaises(MODULE.RegistryTemporarilyUnavailable):
                        MODULE.fetch_catalog_repos()
                self.assertEqual(fetch.call_count, 4)
                self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [2, 4, 8])
                self.assertEqual(MODULE.CATALOG_REPO_CACHE, {})

    def test_retry_after_http_date_and_invalid_header(self):
        with patch.object(MODULE, "datetime") as clock:
            clock.now.return_value = datetime(2026, 10, 24, tzinfo=timezone.utc)
            self.assertEqual(MODULE.registry_retry_delay(0, "Sat, 24 Oct 2026 00:00:12 GMT"), 12)
        for value in ("invalid", "-1", "Fri, 23 Oct 2026 00:00:00 GMT"):
            self.assertEqual(MODULE.registry_retry_delay(0, value), 2)

    def test_long_retry_after_defers_without_retrying_early(self):
        with patch.object(MODULE.urllib.request, "urlopen", side_effect=self.http_error(429, "120")) as fetch:
            with self.assertRaises(MODULE.RegistryTemporarilyUnavailable):
                MODULE.fetch_catalog_repos()
        self.assertEqual(fetch.call_count, 1)
        self.sleep.assert_not_called()

    def test_permanent_registry_errors_and_malformed_json_do_not_retry(self):
        for error in (self.http_error(401), self.http_error(403), self.http_error(404), io.BytesIO(b"not JSON")):
            with self.subTest(error=error), patch.object(MODULE.urllib.request, "urlopen", side_effect=[error]) as fetch:
                with self.assertRaises(MODULE.RegistryCatalogFetchError) as raised:
                    MODULE.fetch_catalog_repos()
                self.assertNotIsInstance(raised.exception, MODULE.RegistryTemporarilyUnavailable)
                self.assertEqual(fetch.call_count, 1)
        self.sleep.assert_not_called()

    def test_github_reads_and_comment_writes_are_not_retried(self):
        for kwargs in ({}, {"method": "POST", "data": {"body": "notice"}}, {"method": "PATCH", "data": {"body": "notice"}}):
            with self.subTest(kwargs=kwargs), patch.object(MODULE.urllib.request, "urlopen", side_effect=self.http_error(429)) as fetch:
                self.assertIsNone(MODULE.api_request("https://api.github.com/example", **kwargs))
                self.assertEqual(fetch.call_count, 1)
        self.sleep.assert_not_called()

    def test_workflow_entrypoint_exits_zero_only_for_temporary_registry_failure(self):
        for status, expected_exit in ((429, 0), (401, 1)):
            responses = [self.response([])] + [
                self.http_error(status) for _ in range(4 if status == 429 else 1)
            ]
            with self.subTest(status=status), patch.dict(os.environ, {
                "GH_TOKEN": "fixture", "PR_NUMBER": "1", "GITHUB_REPOSITORY": "owner/catalog",
                "PR_TITLE": "Add plugin", "PR_AUTHOR": "author",
            }, clear=True), patch.object(MODULE.urllib.request, "urlopen", side_effect=responses) as fetch, patch.object(
                MODULE.subprocess, "run", return_value=CompletedProcess([], 0, stdout="+- [Plugin](https://github.com/owner/plugin)")
            ):
                with self.assertRaises(SystemExit) as exited:
                    runpy.run_path(str(Path(MODULE.__file__)), run_name="__main__")
                self.assertEqual(exited.exception.code, expected_exit)
                self.assertTrue(all(call.args[0].method == "GET" for call in fetch.call_args_list))


class ClaimNoticeTests(unittest.TestCase):
    def test_temporary_failure_defers_both_catalog_phases_without_comment_mutations(self):
        for phase in ("all", "owner_verified"):
            with self.subTest(phase=phase), patch.multiple(
                MODULE, GH_TOKEN="fixture", PR_NUMBER="1", REPO_FULL="owner/catalog",
                PR_AUTHOR="author", PR_TITLE="Add plugin",
            ), patch.object(MODULE, "has_existing_claim_comment", return_value={"id": 123, "deferred": True}), patch.object(
                MODULE, "parse_pr_diff_for_repos", return_value={"owner/plugin"}
            ), patch.object(
                MODULE, "fetch_catalog_repos", side_effect=(
                    [MODULE.RegistryTemporarilyUnavailable("retry budget exhausted")]
                    if phase == "all" else
                    [{"owner/plugin"}, MODULE.RegistryTemporarilyUnavailable("retry budget exhausted")]
                )
            ), patch.object(MODULE, "post_comment") as post, patch.object(MODULE, "update_comment") as update:
                self.assertEqual(MODULE.main(), MODULE.NOTICE_DEFERRED)
                post.assert_not_called()
                update.assert_not_called()

    def test_partially_synced_submissions_only_link_live_registry_repositories(self):
        with patch.multiple(
            MODULE,
            GH_TOKEN="fixture",
            PR_NUMBER="1",
            REPO_FULL="owner/catalog",
            PR_AUTHOR="author",
            PR_TITLE="Add plugins",
        ), patch.object(
            MODULE, "has_existing_claim_comment", return_value=False
        ), patch.object(
            MODULE,
            "parse_pr_diff_for_repos",
            return_value={"owner/synced", "owner/pending"},
        ), patch.object(
            MODULE, "fetch_catalog_repos", side_effect=[{"owner/synced"}, set()]
        ), patch(
            "builtins.open", mock_open(read_data="https://github.com/owner/pending")
        ), patch.object(MODULE, "post_comment", return_value=True) as post:
            self.assertEqual(MODULE.main(), 0)
            post.assert_called_once_with(
                "author", {"owner/pending", "owner/synced"}
            )

    def test_catalog_repository_is_claimable_before_registry_sync(self):
        body = MODULE.build_comment_body(
            "author", repositories=(), pending_repositories={"owner/pending"}
        )
        self.assertIn("Claim your plugin", body)
        self.assertIn("claim=owner%2Fpending", body)
        self.assertIn("Verify ownership of `owner/pending`", body)
        self.assertNotIn("Still syncing", body)
        self.assertNotIn("No action is needed yet", body)

    def test_all_pending_submissions_still_post_sync_status(self):
        with patch.multiple(
            MODULE,
            GH_TOKEN="fixture",
            PR_NUMBER="1",
            REPO_FULL="owner/catalog",
            PR_AUTHOR="author",
            PR_TITLE="Add plugin",
        ), patch.object(
            MODULE, "has_existing_claim_comment", return_value=False
        ), patch.object(
            MODULE, "parse_pr_diff_for_repos", return_value={"owner/pending"}
        ), patch.object(
            MODULE, "fetch_catalog_repos", return_value=set()
        ), patch(
            "builtins.open", mock_open(read_data="https://github.com/owner/pending")
        ), patch.object(MODULE, "post_comment", return_value=True) as post:
            self.assertEqual(MODULE.main(), 0)
            post.assert_called_once_with("author", {"owner/pending"})

    def test_registry_fetch_failure_fails_without_posting_a_notice(self):
        with patch.multiple(
            MODULE,
            GH_TOKEN="fixture",
            PR_NUMBER="1",
            REPO_FULL="owner/catalog",
            PR_AUTHOR="author",
            PR_TITLE="Add plugin",
        ), patch.object(
            MODULE, "has_existing_claim_comment", return_value=False
        ), patch.object(
            MODULE, "parse_pr_diff_for_repos", return_value={"owner/plugin"}
        ), patch.object(
            MODULE,
            "fetch_catalog_repos",
            side_effect=MODULE.RegistryCatalogFetchError("registry unavailable"),
        ), patch.object(MODULE, "post_comment", return_value=True) as post:
            self.assertEqual(MODULE.main(), 1)
            post.assert_not_called()

    def test_partial_catalog_fetch_is_not_treated_as_authoritative_absence(self):
        with patch.object(
            MODULE,
            "api_request",
            side_effect=[
                {"items": [{"sourceRepo": "owner/live"}], "nextCursor": "next"},
                None,
            ],
        ):
            with self.assertRaises(MODULE.RegistryCatalogFetchError):
                MODULE.fetch_catalog_repos()

    def test_pagination_bound_is_not_treated_as_complete(self):
        page = {"items": [], "nextCursor": "next"}
        with patch.object(MODULE, "MAX_CATALOG_PAGES", 2), patch.object(
            MODULE, "api_request", side_effect=[page, page]
        ):
            with self.assertRaises(MODULE.RegistryCatalogFetchError):
                MODULE.fetch_catalog_repos()

    def test_repeated_catalog_cursor_is_not_treated_as_a_complete_catalog(self):
        page = {"items": [], "nextCursor": "50"}
        with patch.object(MODULE, "api_request", side_effect=[page, page]):
            with self.assertRaises(MODULE.RegistryCatalogFetchError):
                MODULE.fetch_catalog_repos()

    def test_catalog_fetch_supports_more_than_ten_pages(self):
        pages = [
            {
                "items": [{"sourceRepo": f"owner/plugin-{index}"}],
                "nextCursor": str(index + 1),
            }
            for index in range(10)
        ]
        pages.append(
            {"items": [{"sourceRepo": "owner/plugin-10"}], "nextCursor": None}
        )
        with patch.object(MODULE, "api_request", side_effect=pages):
            repos = MODULE.fetch_catalog_repos()

        self.assertEqual(len(repos), 11)
        self.assertIn("owner/plugin-10", repos)

    def test_manual_dispatch_loads_trusted_merged_pr_metadata(self):
        with patch.multiple(
            MODULE,
            GH_TOKEN="fixture",
            PR_NUMBER="1",
            REPO_FULL="owner/catalog",
        ), patch.object(
            MODULE,
            "api_request",
            return_value={
                "merged_at": "2026-09-22T00:00:00Z",
                "title": "Add plugin",
                "user": {"login": "author"},
            },
        ):
            self.assertEqual(
                MODULE.fetch_merged_pr_metadata(), ("Add plugin", "author")
            )

    def test_existing_claim_comment_is_found_on_a_later_page(self):
        first_page = [{"body": "unrelated"} for _ in range(100)]
        second_page = [{"body": f"before {MODULE.MARKER} after"}]
        with patch.multiple(
            MODULE,
            GH_TOKEN="fixture",
            PR_NUMBER="1",
            REPO_FULL="owner/catalog",
        ), patch.object(
            MODULE, "api_request", side_effect=[first_page, second_page]
        ):
            self.assertTrue(MODULE.has_existing_claim_comment())

    def test_links_preserve_each_repository_and_attribution(self):
        body = MODULE.build_comment_body("author", ["owner/second", "owner/first"])
        links = re.findall(r"https://hol.org/guard/plugins\?[^)]+", body)
        self.assertEqual(len(links), 2)
        queries = [parse_qs(urlparse(link).query) for link in links]
        self.assertEqual(
            [q["claim"][0] for q in queries], ["owner/first", "owner/second"]
        )
        self.assertTrue(all(q["utm_campaign"] == ["plugin_claim"] for q in queries))
        self.assertIn("Continue with GitHub", body)
        self.assertNotIn("read:org", body)
        self.assertIn("30 seconds", body)
        self.assertIn("🎉", body)

    def test_empty_repository_set_retains_a_usable_dashboard_link(self):
        body = MODULE.build_comment_body("author")
        self.assertIn("[Open your plugin dashboard]", body)
        self.assertIn("already verified", body)
        self.assertNotIn("Claim your plugin", body)
        self.assertNotIn("?claim=", body)


if __name__ == "__main__":
    unittest.main()
