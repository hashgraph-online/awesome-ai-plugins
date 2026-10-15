#!/usr/bin/env python3
"""Publish allowlisted finding metadata, never source text or secret values."""

import argparse
import json
import re
from pathlib import Path, PurePosixPath
from urllib.parse import quote


SEVERITIES = {"critical", "high", "medium", "low", "info"}


def guidance(rule):
    if "SECRET" in rule or "CREDENTIAL" in rule:
        return "Review the credential match. Rotate real credentials; use clearly synthetic test fixtures."
    if "PIN" in rule or "MUTABLE" in rule:
        return "Pin the referenced dependency or action to an immutable version."
    if "EXEC" in rule or "EVAL" in rule or "INJECTION" in rule:
        return "Review untrusted input handling and execution boundaries; report a reproducible false positive for maintainer review."
    return "Review this rule at the reported location. Fix the finding or provide a reproducible explanation for maintainer review."


def safe_path(value, root):
    if not isinstance(value, str):
        return None
    if value.startswith(str(root) + "/"):
        value = value[len(str(root)) + 1:]
    path = PurePosixPath(value)
    if (not value or len(value) > 240 or path.is_absolute() or ".." in path.parts
            or not re.fullmatch(r"[A-Za-z0-9_./@+ -]+", value)
            or re.search(r"ghp_|github_pat_|AKIA[0-9A-Z]{16}", value)):
        return None
    return value


def sanitize(raw, root, source_commit):
    findings = []
    for item in raw.get("findings", []):
        if not isinstance(item, dict):
            continue
        rule = item.get("ruleId", "")
        severity = item.get("severity", "")
        if not isinstance(rule, str) or not re.fullmatch(r"[A-Z0-9_.-]{1,100}", rule) or severity not in SEVERITIES:
            continue
        finding = {"ruleId": rule, "severity": severity, "remediation": guidance(rule)}
        path = safe_path(item.get("filePath"), root)
        if path:
            finding["filePath"] = path
            line = item.get("lineNumber")
            if type(line) is int and 0 < line < 10000000:
                finding["lineNumber"] = line
        findings.append(finding)
    score = raw.get("effective_score", raw.get("score"))
    return {"schema_version": "sanitized-scan.v1",
            "source_commit": source_commit if re.fullmatch(r"[0-9a-f]{40}", source_commit) else None,
            "score": score if type(score) in (int, float) and 0 <= score <= 100 else None,
            "findings": findings,
            "notice": "Source snippets and scanner free-form text are omitted. This report does not override the required scan verdict."}


def sarif(report):
    results = []
    for finding in report["findings"]:
        result = {"ruleId": finding["ruleId"],
                  "level": "error" if finding["severity"] in {"critical", "high"} else "warning",
                  "message": {"text": finding["remediation"]}}
        if finding.get("filePath"):
            location = {"artifactLocation": {"uri": quote(finding["filePath"], safe="/")}}
            if finding.get("lineNumber"):
                location["region"] = {"startLine": finding["lineNumber"]}
            result["locations"] = [{"physicalLocation": location}]
        results.append(result)
    return {"version": "2.1.0", "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
            "runs": [{"tool": {"driver": {"name": "HOL AI Plugin Scanner"}}, "results": results}]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-commit", default="")
    args = parser.parse_args()
    raw = json.loads(args.input.read_text())
    if not isinstance(raw, dict) or not isinstance(raw.get("findings"), list):
        raise ValueError("Scanner did not produce a valid finding report")
    report = sanitize(raw, args.root, args.source_commit)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "findings.json").write_text(json.dumps(report, indent=2) + "\n")
    (args.output_dir / "findings.sarif").write_text(json.dumps(sarif(report), indent=2) + "\n")
    lines = ["## Source scan findings", "", f"Scanned source commit: `{report['source_commit']}`",
             f"Score: {report['score']}", "", report["notice"], "",
             "| Rule | Severity | Location | Next Step |", "| --- | --- | --- | --- |"]
    ordered = sorted(report["findings"], key=lambda f: (f["severity"] not in {"critical", "high"}, f["ruleId"]))
    for finding in ordered[:20]:
        location = finding.get("filePath", "Location unavailable")
        if finding.get("lineNumber"):
            location += f":{finding['lineNumber']}"
        lines.append(f"| `{finding['ruleId']}` | {finding['severity']} | `{location}` | {finding['remediation']} |")
    lines.extend(["", f"{len(report['findings'])} findings total. Download the finding artifact for complete JSON and SARIF."])
    (args.output_dir / "findings.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
