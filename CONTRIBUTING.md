# Contributing to Awesome AI Assistant Extensions

Thank you for considering a contribution!

## Adding Extensions

1. **Search first** - Check if the extension is already listed
2. **Verify it works** - Test the extension before submitting
3. **Follow the format** - Use the existing entry style:
   ```
   - [Extension Name](https://github.com/owner/repo) - Description (max 1 sentence).
   ```
4. **Add to appropriate section** - Codex plugins, Claude Code skills, Gemini extensions, MCP servers, or Cross-AI tools

## Validation

Before submitting:

```bash
# Run the HOL Plugin Scanner (required for all submissions)
pipx run plugin-scanner lint .
pipx run plugin-scanner verify .
```

**Scanner Requirements (Mandatory for This List):**

All plugins submitted to **Awesome AI Plugins** must pass the HOL AI Plugin Scanner:

| Requirement | Threshold |
|-------------|-----------|
| **Score** | ≥ 80 / 130 |
| **Severity** | No critical or high findings |
| **CI** | Scanner must run in your repo's GitHub Actions |

See the full guide: [`SCANNER_GUIDE.md`](./SCANNER_GUIDE.md)  
See contributing requirements: [`CONTRIBUTING.md`](./CONTRIBUTING.md)

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
