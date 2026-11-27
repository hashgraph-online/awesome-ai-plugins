#!/usr/bin/env python3
"""Audit every Community Plugins listing for ecosystem evidence.

The contribution validator protects new pull requests. This audit covers older
catalog rows and produces a machine-readable report without changing the
catalog. A maintainer can use the report to prepare focused reclassification or
removal pull requests.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
import importlib.util
import json
from pathlib import Path
import re
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
VALIDATOR_PATH = REPO_ROOT / "scripts/validate-contribution.py"
PLATFORM_RE = re.compile(r"<!--\s*platform:\s*([a-z0-9-]+)\s*-->")
H2_RE = re.compile(r"^##\s+(.+?)\s*$")
H3_RE = re.compile(r"^###\s+(.+?)\s*$")
GUARD_SIGNAL_PATTERNS = (
    ("system-of-record", re.compile(r"\bsystem of record\b", re.I)),
    ("write-verification", re.compile(r"\b(?:verify|verif(?:ies|y|ied|ication))\b.{0,40}\bwrite|\bwrite.{0,40}\bverif", re.I | re.S)),
    ("receipts", re.compile(r"\b(?:write|audit|signed|proof)\s+receipts?\b|\breceipts?\b", re.I)),
    ("reconciliation", re.compile(r"\breconcil(?:e|iation|ing|es)\b|\bstate integrity\b", re.I)),
    ("approval-controls", re.compile(r"\b(?:approval|approvals|approval gate|human[- ]in[- ]the[- ]loop)\b", re.I)),
    ("drift-detection", re.compile(r"\b(?:drift detection|detect(?:s|ing)? drift|capability drift)\b", re.I)),
    ("audit-trail", re.compile(r"\baudit trail\b|\bprovenance\b", re.I)),
)


@dataclass(frozen=True)
class CatalogEntry:
    display_name: str
    url: str
    owner: str
    repo: str
    description: str
    section: str
    subsection: str
    line: int


@dataclass(frozen=True)
class AuditResult:
    display_name: str
    url: str
    owner: str
    repo: str
    section: str
    subsection: str
    line: int
    platform: str | None
    status: str
    surfaces: tuple[str, ...] = ()
    default_branch: str | None = None
    guard_candidate: bool = False
    guard_signals: tuple[str, ...] = ()
    error: str | None = None


def load_validator() -> Any:
    spec = importlib.util.spec_from_file_location("validate_contribution", VALIDATOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {VALIDATOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def read_catalog_entries(readme: str, validator: Any) -> list[CatalogEntry]:
    """Parse entries beneath Community Plugins and retain source location."""

    entries: list[CatalogEntry] = []
    section = ""
    subsection = ""
    for line_number, line in enumerate(readme.splitlines(), start=1):
        h2 = H2_RE.match(line)
        if h2:
            section = h2.group(1).strip()
            subsection = ""
            continue
        h3 = H3_RE.match(line)
        if h3:
            subsection = h3.group(1).strip()
            continue
        if section != "Community Plugins":
            continue
        match = validator.README_ENTRY_RE.match(line.strip())
        if not match:
            continue
        entries.append(
            CatalogEntry(
                display_name=match.group(1).strip(),
                url=match.group(2).strip(),
                owner=match.group(3),
                repo=match.group(4),
                description=match.group(5).strip(),
                section=section,
                subsection=subsection,
                line=line_number,
            )
        )
    return entries


def platform_marker(entry: CatalogEntry) -> str | None:
    match = PLATFORM_RE.search(entry.description)
    return match.group(1) if match else None


def guard_signals(text: str) -> tuple[str, ...]:
    return tuple(label for label, pattern in GUARD_SIGNAL_PATTERNS if pattern.search(text))


def error_status(error: str) -> str:
    """Classify a validation error without treating API outages as defects."""

    if "does not expose a recognized AI extension surface" in error:
        return "needs-review"
    if "exposes an Agent Skills extension" in error:
        return "needs-reclassification"
    if any(marker in error for marker in (
        "HTTP 403", "HTTP 429", "response is larger than", "could not fetch",
        "GitHub returned invalid JSON", "timed out",
    )):
        return "unavailable"
    if "source repository or workflow directory was not found" in error:
        return "needs-review"
    return "error"


def audit_entry(entry: CatalogEntry, validator: Any) -> AuditResult:
    platform = platform_marker(entry)
    try:
        contribution = validator.Contribution(
            display_name=entry.display_name,
            url=entry.url,
            owner=entry.owner,
            repo=entry.repo,
            description=entry.description,
        )
        default_branch = validator.ensure_public_github_repository(contribution)
        evidence = validator.validate_ecosystem_entry(contribution, default_branch)
        source_readme = ""
        try:
            source_readme = validator.content_file(
                f"{entry.owner}/{entry.repo}", "README.md", default_branch
            )
        except validator.ValidationError:
            pass
        signals = guard_signals(f"{entry.description}\n{source_readme}")
        return AuditResult(
            **{key: getattr(entry, key) for key in (
                "display_name", "url", "owner", "repo", "section", "subsection", "line"
            )},
            platform=platform,
            status="eligible",
            surfaces=evidence.surfaces,
            default_branch=default_branch,
            guard_candidate=bool(signals),
            guard_signals=signals,
        )
    except validator.ValidationError as error:
        return AuditResult(
            **{key: getattr(entry, key) for key in (
                "display_name", "url", "owner", "repo", "section", "subsection", "line"
            )},
            platform=platform,
            status=error_status(str(error)),
            error=str(error),
        )
    except Exception as error:  # pragma: no cover - protects a complete audit from one source
        return AuditResult(
            **{key: getattr(entry, key) for key in (
                "display_name", "url", "owner", "repo", "section", "subsection", "line"
            )},
            platform=platform,
            status="error",
            error=f"unexpected audit error: {error}",
        )


def markdown_report(results: list[AuditResult]) -> str:
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    guard_candidates = [result for result in results if result.guard_candidate]
    lines = [
        "# Community Plugins catalog audit",
        "",
        f"Entries audited: {len(results)}",
        "",
        "## Summary",
        "",
        *(f"- {status}: {count}" for status, count in sorted(counts.items())),
        f"- HOL Guard candidates: {len(guard_candidates)}",
    ]
    review = [result for result in results if result.status not in ("eligible", "unavailable")]
    if review:
        lines += ["", "## Entries requiring review", "", "| Entry | Section | Line | Status | Reason |", "| --- | --- | ---: | --- | --- |"]
        for result in review:
            reason = result.error or "manual review required"
            lines.append(
                f"| [{result.display_name}]({result.url}) | {result.subsection or result.section} | {result.line} | {result.status} | {reason.replace('|', '/')} |"
            )
    if guard_candidates:
        lines += ["", "## HOL Guard candidates", "", "| Entry | Signals |", "| --- | --- |"]
        for result in guard_candidates:
            lines.append(f"| [{result.display_name}]({result.url}) | {', '.join(result.guard_signals)} |")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--readme", type=Path, default=REPO_ROOT / "README.md")
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--limit", type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    validator = load_validator()
    entries = read_catalog_entries(args.readme.read_text(encoding="utf-8"), validator)
    if args.limit is not None:
        entries = entries[: args.limit]
    if args.workers < 1:
        raise SystemExit("--workers must be positive")

    results: list[AuditResult] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(audit_entry, entry, validator): entry for entry in entries}
        for future in as_completed(futures):
            results.append(future.result())
            if len(results) == len(entries) or len(results) % 25 == 0:
                print(f"Audited {len(results)}/{len(entries)}", file=sys.stderr)
    results.sort(key=lambda result: result.line)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps([asdict(result) for result in results], indent=2) + "\n",
        encoding="utf-8",
    )
    args.output_markdown.write_text(markdown_report(results), encoding="utf-8")
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    print(f"Audited {len(results)} entries: {counts}")
    return 1 if any(result.status in ("needs-review", "needs-reclassification", "error") for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
