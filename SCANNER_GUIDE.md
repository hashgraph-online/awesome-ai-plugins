# HOL Plugin Scanner Guide

A quick guide to using the [HOL Plugin Scanner](https://github.com/hashgraph-online/hol-guard) for AI plugin validation.

## Installation

```bash
pipx install plugin-scanner
```

Or run without installing:

```bash
pipx run plugin-scanner lint .
pipx run plugin-scanner verify .
```

## Basic Usage

### Lint a plugin directory

```bash
plugin-scanner lint /path/to/plugin
```

### Verify with full scoring

```bash
plugin-scanner verify /path/to/plugin --format text
```

### Output formats

- `text` — Human-readable (default)
- `json` — Machine-readable for CI/CD
- `sarif` — For GitHub Security tab integration

## Scoring System

The scanner reports a normalized score from **0 to 100**. It evaluates security,
operational hardening, metadata, best practices, marketplace/package surfaces,
skill security, and code quality according to the detected ecosystem. The raw
point budget is not a fixed denominator: checks and package surfaces can vary by
ecosystem and scan target, and the scanner normalizes the points returned by the
checks for that scan to the 0–100 score.

For DeepSeek Harness packages, the scanner evaluates the repository and package
surfaces even though the runtime uses Cordis and `dsh.bundle` rather than a
Codex manifest.

| Category | Key Checks |
|----------|------------|
| Manifest / package metadata | Valid ecosystem metadata, required fields, ID and package format |
| Security | No secrets, no dangerous code, hardened transports, policies |
| Operational Security | CI/CD pinned, dependency hygiene, no overly broad permissions |
| Best Practices | README, tests, linting, license |
| Marketplace | Proper versioning, tags, clean distribution metadata |
| Skill Security | Hooks, env vars, validation, no hardcoded paths |
| Code Quality | No dangerous dynamic execution or shell-injection patterns |

**Passing criteria:** normalized score ≥ 80/100, with no critical or high severity findings.

### Grok plugin checks

Grok Build plugins should keep their native `.grok-plugin/plugin.json` manifest
when the project uses one, document the tested `grok plugin install
owner/repo --trust` flow, and keep the same scanner CI gate as every other
ecosystem. The scanner result is a repository safety baseline; it does not
replace Grok's runtime validation.

### Kimi plugin checks

Kimi Code plugins may use `kimi.plugin.json`, `.kimi-plugin/plugin.json`, or the
older `plugin.json` manifest. Document the tested `/plugins install
https://github.com/owner/repo` flow and keep the same scanner CI gate. The
scanner result is a repository safety baseline; it does not replace Kimi's
runtime validation.

### DeepSeek Harness package checks

DeepSeek Harness plugins should keep the installable `dsh.bundle` declaration in
`package.json`, export a Cordis `apply(ctx)` entry point, and document a tested
`dsh plugin add <package-or-github-spec>` flow. Scanner CI is optional for
every ecosystem and recommended for security. HOL still scans listed projects independently.

## CI/CD Integration

### GitHub Actions

```yaml
name: Plugin Security Scan
on: [pull_request, push]
permissions:
  contents: read
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
        with:
          persist-credentials: false
      - uses: hashgraph-online/ai-plugin-scanner-action@432eebe0fb9212be97c8d15cb1da9668a91e7914 # v1.2.531
        with:
          plugin_dir: "."
          min_score: 80
          fail_on_severity: high
```

### Supply-chain properties

The recommended workflow is intentionally constrained:

- `contents: read` is the only GitHub permission;
- no repository secrets are required;
- checkout credentials are not persisted;
- online probing and SARIF upload are disabled unless explicitly enabled;
- the scanner action and its dependencies are pinned;
- the scanner wheel is checked against a committed SHA-256 and verified PyPI provenance before installation.

Review the [action source](https://github.com/hashgraph-online/ai-plugin-scanner-action) and pinned commit before enabling it. A passing result is a consistent baseline for community review, not a claim that software is risk-free.

### Recommended for Awesome AI Plugins listing

Scanner CI is optional for listing. HOL still scans listed projects independently. We recommend including it so MCP servers, skills, plugins, and other agent extensions stay continuously checked. Projects that maintain scanner CI receive the full trust score; projects without it remain eligible and receive a 10% trust-score reduction.

Add the scanner badge to your README:

```markdown
[![HOL Guard Scanner](https://img.shields.io/badge/HOL%20Guard-passing-00a67e)](https://github.com/hashgraph-online/hol-guard)
```

## Common Fixes

| Finding | Fix |
|---------|-----|
| Missing SECURITY.md | Add a SECURITY.md with supported versions and reporting process |
| Missing LICENSE | Add an open-source license (Apache 2.0, MIT, etc.) |
| Hardcoded secrets | Move to environment variables or use secret management |
| Dangerous eval() | Refactor to remove dynamic code execution |
| Unpinned GitHub Actions | Use commit SHA instead of `@v1` |
| Missing Dependabot | Add `.github/dependabot.yml` |

## Getting Help

- [HOL Guard Issues](https://github.com/hashgraph-online/hol-guard/issues)
- [HOL Plugin Registry](https://hol.org/registry/plugins) — Browse plugins with trust scores
- [HOL Guard Examples](https://github.com/zerocodefast/hol-guard-examples) — Security guides and tutorials
