# Contributing to Awesome AI Assistant Extensions

Thank you for considering a contribution!

## Adding Extensions

1. **Search first** - Check if the extension is already listed
2. **Verify it works** - Test the extension before submitting
3. **Follow the format** - Use the existing entry style:
   ```
   - [Extension Name](https://github.com/owner/repo) - Description (max 1 sentence).
   ```
4. **Add to appropriate section** - Codex plugins, Claude Code skills, Gemini extensions, Grok plugins, Kimi plugins, DeepSeek Harness plugins, MCP servers, or Cross-AI tools

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

Scanner CI is optional. HOL scans listed projects independently. Projects that maintain the scanner in their own CI receive the full trust score; projects without it remain eligible and receive a 10% trust-score reduction for missing continuous security verification.

See the full guide: [`SCANNER_GUIDE.md`](./SCANNER_GUIDE.md)

### Why scanner CI is still useful

AI extensions can register hooks, execute commands, read environment variables, and influence an agent's behavior. A compromised extension can therefore affect users outside the original repository. Scanner CI gives every community listing the same reproducible baseline for identifying:

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
HOL clones and scans each valid new source repository; those scan results are
advisory and do not block a valid listing.

Existing open pull requests are covered by the scheduled and manually
dispatchable `.github/workflows/sweep-open-prs.yml` workflow. It reviews each
PR's exact README base/head revisions without executing fork code, queues an
advisory scan, and publishes the result on each PR head. When the source
repository has no scanner CI, the bot still comments: listing can merge, adding
the action is optional, and the comment explains the trust-score benefit.
Reruns update the existing bot comment and check in place.

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

## Guidelines

- No dead links or unmaintained projects
- Describe what the extension does, not just name it
- Keep descriptions concise (one sentence)
- Use alphabetical order within sections
- Community plugins should have some activity (recent commits or releases)
