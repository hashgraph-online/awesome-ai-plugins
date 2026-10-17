#!/usr/bin/env python3
"""Gate a catalog scan on its numeric score, not advisory finding severity."""

from __future__ import annotations

import os
import sys
from decimal import Decimal, InvalidOperation

MINIMUM_SCORE = Decimal("80")
MAXIMUM_SCORE = Decimal("100")


def evaluate_score(raw_score: str) -> tuple[bool, str]:
    """Return whether a scanner output establishes the required score."""

    value = raw_score.strip()
    if not value:
        return False, "The centralized scanner did not establish a numeric score."

    try:
        score = Decimal(value)
    except InvalidOperation:
        return False, "The centralized scanner returned an invalid numeric score."

    if not score.is_finite() or score < 0 or score > MAXIMUM_SCORE:
        return False, "The centralized scanner returned a score outside the 0-100 range."
    if score < MINIMUM_SCORE:
        return False, f"Centralized scan score {score} is below the required minimum of 80."
    return True, f"Centralized scan score {score} meets the required minimum of 80."


def main() -> int:
    """Enforce the score exported by the pinned scanner action."""

    passed, message = evaluate_score(os.environ.get("SCANNER_SCORE", ""))
    print(message if passed else f"::error::{message}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
