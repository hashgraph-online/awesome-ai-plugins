import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "enforce_scan_score",
    Path(__file__).resolve().parents[1] / "scripts/enforce-scan-score.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class EnforceScanScoreTests(unittest.TestCase):
    def test_threshold_and_higher_scores_pass(self):
        for raw_score in ("80", "85", "100", "80.5"):
            with self.subTest(score=raw_score):
                passed, message = MODULE.evaluate_score(raw_score)
                self.assertTrue(passed)
                self.assertIn("meets the required minimum of 80", message)

    def test_below_threshold_scores_fail(self):
        for raw_score in ("0", "79", "79.99"):
            with self.subTest(score=raw_score):
                passed, message = MODULE.evaluate_score(raw_score)
                self.assertFalse(passed)
                self.assertIn("below the required minimum of 80", message)

    def test_missing_or_invalid_scores_fail_closed(self):
        for raw_score in ("", "unknown", "NaN", "Infinity", "101", "-1"):
            with self.subTest(score=raw_score):
                passed, _message = MODULE.evaluate_score(raw_score)
                self.assertFalse(passed)


if __name__ == "__main__":
    unittest.main()
