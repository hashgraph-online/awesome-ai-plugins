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

The scanner checks 7 categories totaling 142 points:

| Category | Points | Key Checks |
|----------|--------|------------|
| Manifest | 31 | Valid plugin.json, required fields, ID format |
| Security | 36 | No secrets, no dangerous code, HTTPS, policies |
| Operational | 20 | CI/CD pinned, Dependabot, no overly broad permissions |
| Best Practices | 15 | README, tests, linting, license |
| Marketplace | 15 | Proper versioning, tags, clean history |
| Skill Security | 15 | Hooks, env vars, validation, no hardcoded paths |
| Code Quality | 10 | No TODOs, no debug code, consistent style |

**Passing criteria:** Score ≥ 80/142, with no critical or high severity findings.

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
      - uses: hashgraph-online/ai-plugin-scanner-action@55616c962cf86368423f7673b2ecdfdbe613d1af # v1.2.515
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

### Required for Awesome AI Plugins listing

All plugins submitted to this list must:

1. **Score ≥ 80/142** in the scanner
2. **Have zero critical or high findings**
3. **Run the scanner in CI/CD** (GitHub Actions preferred)

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
