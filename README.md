<p align="center">
  <br>
  <img width="80" src="https://cdn.rawgit.com/sindresorhus/awesome/d7305f38d29fed78fa85652e3a63e154dd8e8829/media/badge.svg" alt="Awesome">
  <br>
</p>

<h1 align="center">Awesome AI Plugins</h1>

<p align="center">A curated list of awesome plugins for AI assistants.</p>

<p align="center">
  <a href="http://makeapullrequest.com"><img src="https://img.shields.io/badge/PRs-welcome-brightgreen.svg" alt="PRs Welcome"></a>
  <a href="https://opensource.org/licenses/Apache-2.0"><img src="https://img.shields.io/badge/License-Apache_2.0-blue.svg" alt="License"></a>
  <a href="https://hol.org/registry/plugins"><img src="https://img.shields.io/badge/Browse-Registry-green" alt="Browse Registry"></a>
</p>

<p align="center">
  This list covers plugins for <strong>OpenAI Codex</strong>, <strong>Claude Code</strong>, <strong>OpenCode</strong>, <strong>Google Gemini CLI</strong>, and cross-platform <strong>MCP servers</strong> that work across multiple AI assistants.
</p>

<br>

## Contents

- [Start Here](#start-here)
- [OpenAI Codex Plugins](#openai-codex-plugins)
- [Claude Code Plugins](#claude-code-plugins)
- [OpenCode Plugins](#opencode-plugins)
- [Google Gemini CLI Plugins](#google-gemini-cli-plugins)
- [MCP Servers (Cross-Platform)](#mcp-servers-cross-platform)
- [Related Projects](#related-projects)
- [Contributing](#contributing)

---

## Start Here

This list covers the ecosystem of AI assistant plugins. Each platform has its own format:

| Platform | Format | Repository |
|----------|--------|------------|
| OpenAI Codex | `.codex-plugin/plugin.json` + skills | [awesome-codex-plugins](https://github.com/hashgraph-online/awesome-codex-plugins) |
| Claude Code | Skills (`SKILL.md` + tools) | [anthropics/skills](https://github.com/anthropics/skills) |
| OpenCode | Plugins (`.opencode/`) | [awesome-opencode-plugins](https://github.com/awesome-opencode/awesome-opencode-plugins) |
| Gemini CLI | Extensions (`.gemini/`) | [Piebald-AI/awesome-gemini-cli-extensions](https://github.com/Piebald-AI/awesome-gemini-cli-extensions) |
| Cross-AI | MCP servers (`mcp.json`) | [punkpeye/awesome-mcp-servers](https://github.com/punkpeye/awesome-mcp-servers) |

### Quick Validation

Before publishing plugins, validate them:

```bash
# Codex plugins
pipx run codex-plugin-scanner lint .

# MCP servers
npx @modelcontextprotocol/server-validator
```

---

## OpenAI Codex Plugins

OpenAI Codex plugins package skills, MCP servers, and app integrations into shareable, installable bundles. See the dedicated list: [awesome-codex-plugins](https://github.com/hashgraph-online/awesome-codex-plugins)

<details>
<summary>Curated by OpenAI — available in the built-in Codex Plugin Directory</summary>

- Box - Access and manage files.
- Cloudflare - Manage Workers, Pages, DNS, and infrastructure.
- Figma - Inspect designs, extract specs, and document components.
- GitHub - Review changes, manage issues, and interact with repositories.
- Gmail - Read, search, and compose emails.
- Google Drive - Edit and manage files in Google Drive.
- Hugging Face - Browse models, datasets, and spaces.
- Linear - Create and manage issues, projects, and workflows.
- Notion - Create and edit pages, databases, and content.
- Sentry - Monitor errors, triage issues, and track performance.
- Slack - Send messages, search channels, manage conversations.
- Vercel - Deploy, preview, and manage Vercel projects.

</details>

### Community Plugins

Third-party plugins built by the community.

- [AgentOps](https://github.com/boshu2/agentops) - DevOps layer for coding agents with flow, feedback, and memory.
- [Apple Productivity](https://github.com/matk0shub/apple-productivity-mcp) - Local Apple Calendar and Reminders for macOS.
- [Bitbucket CLI](https://github.com/avivsinai/bitbucket-cli) - Manage Bitbucket repos, PRs, and pipelines.
- [Blueprint](https://github.com/JuliusBrussee/blueprint) - Specification-driven development pipeline with testable acceptance criteria.
- [Brooks Lint](https://github.com/hyhmrright/brooks-lint) - AI code reviews grounded in six classic engineering books.
- [Claude Code for Codex](https://github.com/sendbird/cc-plugin-codex) - Use Claude Code from Codex for reviews and rescue tasks.
- [Claude Octopus](https://github.com/nyldn/claude-octopus) - Multi-LLM orchestration dispatching to 8 providers.
- [Codex Agenteam](https://github.com/yimwoo/codex-agenteam) - Specialist AI agents orchestrated as a configurable team pipeline.
- [HOTL Plugin](https://github.com/yimwoo/hotl-plugin) - Human-on-the-Loop AI coding workflow plugin.
- [Langfuse Observability](https://github.com/avivsinai/langfuse-mcp) - Query traces, debug exceptions, analyze sessions.
- [Registry Broker](https://github.com/hashgraph-online/registry-broker-codex-plugin) - Delegate tasks to specialist AI agents via the HOL Registry.
- [Remotion Plugin](https://github.com/tim-osterhus/codex-remotion-plugin) - Build parameterized Remotion videos in Codex.

---

## Claude Code Plugins

Claude Code extends Anthropic's CLI with custom skills and tools. The official skill repository: [anthropics/skills](https://github.com/anthropics/skills)

### Official Skills

- [Agent Skills](https://github.com/anthropics/skills) - Public repository for Claude Code agent skills.

### Community Skills

#### Development & Workflow

- [Claude Code Harness](https://github.com/dadwadw233/claude-code-harness) - Blueprint skill for turning vague agent ideas into concrete designs.
- [Claude Code Skills](https://github.com/alirezarezvani/claude-skills) - 223 production-ready skills, 23 agents, and 298 Python tools.
- [Claude Octopus](https://github.com/nyldn/claude-octopus) - Multi-LLM orchestration dispatching to Codex, Gemini, Copilot, Qwen, Perplexity, OpenRouter, Ollama, OpenCode.
- [Codex Reviewer](https://github.com/schuettc/codex-reviewer) - Second-pass review of Claude-driven plans.

---

## OpenCode Plugins

Plugins for OpenAI's OpenCode. See: [awesome-opencode-plugins](https://github.com/awesome-opencode/awesome-opencode-plugins)

### Official

- [OpenCode](https://opencode.com) - OpenAI's AI coding CLI.

### Community Plugins

_Contributions welcome - submit via PR_

---

## Google Gemini CLI Plugins

Plugins for Google's Gemini CLI. See: [Piebald-AI/awesome-gemini-cli-extensions](https://github.com/Piebald-AI/awesome-gemini-cli-extensions)

### Official

- [Gemini CLI](https://github.com/google/gemini-cli) - Official Gemini CLI and extension system.

### Community Plugins

_Contributions welcome - submit via PR_

---

## MCP Servers (Cross-Platform)

Model Context Protocol (MCP) servers work across multiple AI assistants that support the protocol. See the comprehensive list: [punkpeye/awesome-mcp-servers](https://github.com/punkpeye/awesome-mcp-servers) (84k stars)

### Popular MCP Servers

#### Development

- [Brave Search](https://github.com/modelcontextprotocol/server-brave-search) - Web search via Brave API.
- [Claude Code MCP](https://github.com/anthropics/mcp-claude-code) - Official MCP server for Claude Code.
- [Codex MCP](https://github.com/openai/mcp-codex) - Official MCP server for Codex.
- [Filesystem](https://github.com/modelcontextprotocol/server-filesystem) - Read and write to local filesystem.
- [GitHub](https://github.com/modelcontextprotocol/server-github) - Manage GitHub repos, issues, PRs.

#### Data & APIs

- [Google Maps](https://github.com/modelcontextprotocol/server-google-maps) - Location and mapping services.
- [PostgreSQL](https://github.com/modelcontextprotocol/server-postgres) - Database queries and operations.
- [Puppeteer](https://github.com/modelcontextprotocol/server-puppeteer) - Browser automation.
- [Slack](https://github.com/modelcontextprotocol/server-slack) - Slack workspace interactions.

### MCP Clients

- [Claude Desktop](https://claude.ai/download) - Anthropic's desktop app with MCP integration.
- [Codex](https://openai.com/codex) - OpenAI's CLI with MCP support.
- [Cursor](https://cursor.sh) - AI-powered code editor with MCP support.
- [Windsurf](https://windsurf.ai) - Codeium's AI agent with MCP support.

---

## Related Projects

- [awesome-ai-agents](https://github.com/e2b-dev/awesome-ai-agents) - AI agent frameworks and tools (27k stars)
- [awesome-codex-plugins](https://github.com/hashgraph-online/awesome-codex-plugins) - Codex-specific plugin list
- [awesome-llm-apps](https://github.com/Shubhamsaboo/awesome-llm-apps) - LLM app examples (104k stars)
- [awesome-mcp-clients](https://github.com/punkpeye/awesome-mcp-clients) - MCP client applications
- [awesome-mcp-servers](https://github.com/punkpeye/awesome-mcp-servers) - Comprehensive MCP server list (84k stars)
- [codex-plugin-scanner](https://github.com/hashgraph-online/codex-plugin-scanner) - Codex plugin quality gate
- [HOL Registry](https://hol.org/registry) - Discover and install plugins

---

## Contributing

PRs welcome! Please follow the contribution guidelines and ensure plugins are validated before submitting.

```bash
# Validate Codex plugins
pipx run codex-plugin-scanner lint .
pipx run codex-plugin-scanner verify .
```

## License

[Apache 2.0](./LICENSE) — Hashgraph Online
