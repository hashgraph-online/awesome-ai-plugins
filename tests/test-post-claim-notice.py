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
    def test_partially_synced_submissions_include_pending_readme_repositories(self):
        with patch.multiple(MODULE, GH_TOKEN="fixture", PR_NUMBER="1", REPO_FULL="owner/catalog", PR_AUTHOR="author", PR_TITLE="Add plugins"), \
                patch.object(MODULE, "has_existing_claim_comment", return_value=False), \
                patch.object(MODULE, "parse_pr_diff_for_repos", return_value={"owner/synced", "owner/pending"}), \
                patch.object(MODULE, "fetch_catalog_repos", side_effect=[{"owner/synced"}, set()]), \
                patch("builtins.open", mock_open(read_data="https://github.com/owner/pending")), \
                patch.object(MODULE, "post_comment", return_value=True) as post:
            self.assertEqual(MODULE.main(), 0)
            post.assert_called_once_with("author", {"owner/synced", "owner/pending"})

    def test_links_preserve_each_repository_and_attribution(self):
        body = MODULE.build_comment_body("author", ["owner/second", "owner/first"])
        links = re.findall(r"https://hol.org/guard/plugins\?[^)]+", body)
        self.assertEqual(len(links), 2)
        queries = [parse_qs(urlparse(link).query) for link in links]
        self.assertEqual([q["claim"][0] for q in queries], ["owner/first", "owner/second"])
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
