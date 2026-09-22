import importlib.util
import re
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import mock_open, patch

SPEC = importlib.util.spec_from_file_location(
    "post_claim_notice", Path(__file__).resolve().parents[1] / "scripts/post-claim-notice.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ClaimNoticeTests(unittest.TestCase):
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
                "author", {"owner/synced"}, {"owner/pending"}
            )

    def test_pending_repository_has_no_claim_link(self):
        body = MODULE.build_comment_body(
            "author", repositories=(), pending_repositories={"owner/pending"}
        )
        self.assertIn("`owner/pending`", body)
        self.assertIn("Still syncing", body)
        self.assertIn("No action is needed yet", body)
        self.assertNotIn("claim=owner%2Fpending", body)
        self.assertNotIn("Verify ownership of `owner/pending`", body)

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
            post.assert_called_once_with("author", set(), {"owner/pending"})

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
        self.assertNotIn("30 seconds", body)

    def test_empty_repository_set_retains_a_usable_dashboard_link(self):
        body = MODULE.build_comment_body("author")
        self.assertIn("[Open the plugin dashboard]", body)
        self.assertNotIn("?claim=", body)


if __name__ == "__main__":
    unittest.main()
