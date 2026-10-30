# Contributing to Awesome AI Assistant Extensions

Thank you for considering a contribution!

## Adding Extensions

1. **Search first** - Check if the extension is already listed
2. **Verify it works** - Test the extension before submitting
3. **Follow the format** - Use the existing entry style:
   ```
   - [Extension Name](https://github.com/owner/repo) - Description (max 1 sentence).
   ```
4. **Add to the appropriate section** - Agent Skills, a platform-specific plugin subsection, or the shared Development & Workflow / Tools & Integrations categories

Entries in the shared Community Plugins categories are ecosystem-neutral by
default. A Codex `.codex-plugin/plugin.json` manifest is not required; the
generated catalogs only advertise a client-specific install target when the
listing identifies that platform or a supported manifest is actually present.

### Native Agent Skills

Use an `Agent Skills` subsection under Community Plugins for repositories that publish independently installable `SKILL.md` folders rather than a client-specific plugin bundle. The generator records `platform: agent-skills`, links `installation_url` to the source README, and omits a client-specific `install_url`. The source README must document tested installation instructions and supported clients.

### Native MCP servers

Keep native MCP servers in the relevant topical category and begin their
description with `MCP server: `. The generator then emits `platform: mcp` and an
`installation_url` pointing to the source project's README. Both registry feeds
retain this documentation link without generating a client-specific
`install_url`. The project's README must document the tested installation and
MCP configuration; this catalog does not execute those instructions or promise
automatic installation.

### Standalone tools

For native applications and standalone CLIs in Tools & Integrations, append
`<!-- platform: standalone -->` to the listing line. The catalog records the
`standalone` platform and repository URL, omits the plugin `install_url`, and
leaves installation instructions in the source repository. Do not add a plugin
manifest unless the project actually supports that plugin runtime.

### Grok submissions

Grok Build plugins can package skills, commands, agents, hooks, MCP servers, or
LSP configuration. Include the repository's native `.grok-plugin/plugin.json`
when the project uses one and document the tested
`grok plugin install owner/repo --trust` flow. Review the [official xAI plugin
marketplace](https://github.com/xai-org/plugin-marketplace) and [Grok plugin
guide](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/09-plugins.md)
before submitting.

### Kimi submissions

Kimi Code plugins can package skills, agents, and MCP servers. Current bundles
use `kimi.plugin.json`; older bundles may use `.kimi-plugin/plugin.json` or
`plugin.json`. Document a tested `/plugins install
https://github.com/owner/repo` flow and follow the [official Kimi plugin
documentation](https://github.com/MoonshotAI/kimi-code/blob/main/docs/en/customization/plugins.md)
before submitting.

### DeepSeek Harness submissions

DeepSeek Harness (DSH) plugins are Cordis modules or npm packages. A listed
package must expose an `apply(ctx)` plugin entry point and declare an installable
`dsh.bundle` in `package.json`; a Codex `.codex-plugin/plugin.json` manifest is
not required. Use the exact GitHub repository URL in the README and document the
package name or `dsh plugin add` command in that repository's README.

## What an accepted listing provides

Accepted extensions can be indexed in the [HOL Plugin Registry](https://hol.org/registry/plugins), where they receive a dedicated public profile with trust signals and links to the project.

The registry profile includes a standard **dofollow backlink** to the extension's repository or homepage. This gives search engines a crawlable reference from HOL and may improve discovery and SEO, although no search ranking is guaranteed.

## Validation

Before submitting:

```bash
# Optional local preflight
pipx run plugin-scanner lint .
pipx run plugin-scanner verify .
```

Scanner CI in the source repository is optional for listing. The catalog runs its own required source scan: it must establish a normalized score of at least 75 before merge. Finding severities are advisory for listing eligibility and remain visible for maintainer review. An unavailable score blocks merge; non-gating action failures are advisory. We recommend maintainer-owned CI for continuous checks on MCP servers, skills, plugins, and other agent extensions. Projects that maintain scanner CI receive the full Registry trust score; projects without it receive a 10% trust-score reduction.

See the full guide: [`SCANNER_GUIDE.md`](./SCANNER_GUIDE.md)

### Why we recommend scanner CI

AI extensions — MCP servers, skills, plugins, and related agent tools — can register hooks, execute commands, read environment variables, and change an agent's behavior. A compromised extension can therefore affect users outside the original repository. Maintainer-owned scanner CI is the strongest way to keep those packages continuously checked, and we recommend adding it even though listing does not require it.

Scanner CI gives every community listing the same reproducible baseline for identifying:

- committed secrets and unsafe credential handling;
- dangerous hooks or command execution;
- overly broad GitHub Actions permissions;
- unpinned third-party actions and dependencies;
- malformed or misleading extension metadata.

A passing scan is not a guarantee that an extension is harmless. It is reviewable evidence that helps maintainers catch common supply-chain risks. HOL still scans listed projects independently.

### What the scanner workflow can access

The recommended workflow:

- grants only `contents: read`;
- does not require repository secrets or write permissions;
- keeps live network probing disabled by default;
- installs a fixed scanner release;
- verifies the scanner wheel's SHA-256 and PyPI provenance;
- uploads SARIF only when the maintainer explicitly enables it.

The example in [`SCANNER_GUIDE.md`](./SCANNER_GUIDE.md) pins GitHub Actions to immutable commit SHAs so a mutable tag cannot silently change the code executed by the workflow.

Pull requests that add a Community Plugin entry, including the DeepSeek Harness
Plugins subsection, are checked automatically by
`.github/workflows/validate-contribution.yml`. Catalog format, section, and
discovery checks are required. Scanner CI in the source repository is optional.
HOL clones and scans each valid new source repository. The centralized scan
must pass before the listing can merge; the source repository does not need
its own scanner workflow.

Existing open pull requests are covered by the scheduled and manually
dispatchable `.github/workflows/sweep-open-prs.yml` workflow. It reviews each
PR's exact README base/head revisions without executing fork code, queues a
required source scan, and publishes the result on each PR head. When the source
repository has no scanner CI, the bot recommends the action and explains the
trust-score benefit. Missing scanner CI does not replace or waive the required
centralized scan.
Reruns update the existing bot comment and check in place.

### First-time fork workflow approval

GitHub Actions may report `action_required` for a pull request from a first-time
outside contributor until an authorized repository maintainer approves the
workflow run. That state means the job has **not run**; it is neither a passing
check nor a measured scanner failure. The original contributor cannot approve
their own run by editing the PR or switching accounts. Maintainers should
review the fork's proposed changes and use GitHub's normal workflow-approval
controls if appropriate. Separately, the catalog's trusted centralized sweep
can collect source scan evidence without running code supplied by the fork.
A passing centralized score still does not waive CI requirements or independent
human/security review, and no catalog listing is published solely by this gate.

Use scanner outputs as evidence for maintainers/reviewers:
- Structural lint results
- Publish-readiness verification output
- SARIF/findings for CI and code scanning

The score is best used as a quick trust signal and triage summary (not the only readiness signal).

Open a PR with:
- Clear description of the extension
- Link to the repository
- Category section where it should be added
- Brief verification that it works

## Optional: contribute a Guard profile

If your project exposes CLI commands or MCP tools, you may also contribute an
opt-in profile to [HOL Guard](https://github.com/hashgraph-online/hol-guard).
A profile can request human approval before a specific consequential action
when a user runs your tool through an AI agent. Local protection is free and
does not require an account. Community profiles remain off until locally enabled.

The [Extension Builder](https://github.com/hashgraph-online/hol-guard/blob/main/docs/guard/extension-builder/README.md)
generates contribution files from saved CLI metadata or an exported MCP tool
list. You review the policy and behavior cases, then submit your own PR to HOL
Guard. Your source repository needs no code or scanner-CI changes for this
separate contribution.

To request a guide for your project, check the optional guide request in this
repository's PR template. You can also begin with the
[MCP contribution guide](https://github.com/hashgraph-online/hol-guard/blob/main/docs/guard/mcp-server-contributions.md)
or the [command contribution guide](https://github.com/hashgraph-online/hol-guard/blob/main/docs/guard/extensions/contributing.md).
The guide request does not guarantee that a useful profile can be established.

This is separate from listing acceptance, required central scans, Registry
trust scoring, and the listing's backlink. You do not need to contribute a Guard
profile or request a guide to receive any listing benefit.

## Guidelines

- No dead links or unmaintained projects
- Describe what the extension does, not just name it
- Keep descriptions concise (one sentence)
- Use alphabetical order within sections
- Community plugins should have some activity (recent commits or releases)

# Local development

Use Python 3.12.10 (`.python-version`) and install the pinned development dependency:

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m unittest tests/test-generate-plugins-json.py
python3 scripts/check-alphabetical.py README.md
```

These unit and ordering checks are offline. Contribution validation and source scanning contact GitHub and the repositories being listed. Feed generation can also read upstream catalogs and probe plugin repositories. To exercise the generator without network access or touching tracked output files, run:

```bash
python3 scripts/generate_plugins_json.py --offline --output-dir /tmp/awesome-ai-plugins-preview
```

Review generated files before copying them into the repository. CI validates changes and never commits or pushes generated artifacts.
