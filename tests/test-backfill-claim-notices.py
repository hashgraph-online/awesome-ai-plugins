import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "backfill_claim_notices",
    Path(__file__).resolve().parents[1] / "scripts/backfill-claim-notices.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class BackfillClaimNoticeTests(unittest.TestCase):
    def test_registry_outage_stops_backfill_gracefully_before_remaining_prs(self):
        pull_requests = [
            {"number": number, "title": "Add plugin", "user": {"login": "author"}}
            for number in (1, 2)
        ]
        with patch.dict(os.environ, {"GH_TOKEN": "fixture", "GITHUB_REPOSITORY": "owner/catalog"}), patch.object(
            MODULE, "list_merged_prs", return_value=pull_requests
        ), patch.object(MODULE.NOTICE, "main", return_value=MODULE.NOTICE.NOTICE_DEFERRED) as notice:
            self.assertEqual(MODULE.main(), 0)
            notice.assert_called_once()

    def test_lists_all_pages_in_the_requested_merged_pr_window(self):
        first_page = {
            "total_count": 101,
            "items": [{"number": index} for index in range(1, 101)],
        }
        second_page = {"total_count": 101, "items": [{"number": 101}]}
        with patch.object(
            MODULE.NOTICE, "api_request", side_effect=[first_page, second_page]
        ):
            pull_requests = MODULE.list_merged_prs(
                "owner/catalog", "fixture", "2026-09-18", 1000
            )

        self.assertEqual(len(pull_requests), 101)
        self.assertEqual(pull_requests[-1]["number"], 101)

    def test_refuses_a_partial_backfill(self):
        with patch.object(
            MODULE.NOTICE,
            "api_request",
            return_value={"total_count": 2, "items": []},
        ):
            with self.assertRaisesRegex(RuntimeError, "exceeds BACKFILL_MAX_PRS"):
                MODULE.list_merged_prs("owner/catalog", "fixture", "2026-09-18", 1)

    def test_rejects_incomplete_github_search_results(self):
        with patch.object(
            MODULE.NOTICE,
            "api_request",
            return_value={
                "total_count": 1,
                "incomplete_results": True,
                "items": [{"number": 1}],
            },
        ):
            with self.assertRaisesRegex(RuntimeError, "incomplete merged"):
                MODULE.list_merged_prs("owner/catalog", "fixture", "2026-09-18", 1000)


if __name__ == "__main__":
    unittest.main()
