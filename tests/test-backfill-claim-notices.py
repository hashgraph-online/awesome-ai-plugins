import importlib.util
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


if __name__ == "__main__":
    unittest.main()
