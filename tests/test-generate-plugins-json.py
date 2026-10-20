from __future__ import annotations

import importlib.util
import json
import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from datetime import date
from pathlib import Path
from unittest.mock import patch

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts/generate_plugins_json.py"
SPEC = importlib.util.spec_from_file_location("generate_plugins_json", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

class FakeResponse:
    """Minimal stand-in for the object `urllib.request.urlopen` yields."""

    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


class GeneratorOutputSafetyTests(unittest.TestCase):
    def test_offline_generation_writes_only_to_requested_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            before = {path: path.read_bytes() for path in (MODULE.OUTPUT, MODULE.MARKETPLACE_OUTPUT) if path.exists()}
            with contextlib.redirect_stdout(io.StringIO()), patch(
                "urllib.request.urlopen", side_effect=AssertionError("offline mode attempted network access")
            ):
                MODULE.main(["--offline", "--output-dir", str(output)])
            self.assertTrue((output / "plugins.json").is_file())
            self.assertTrue((output / ".agents/plugins/marketplace.json").is_file())
            for path, content in before.items():
                self.assertEqual(path.read_bytes(), content)

    def test_check_mode_does_not_create_missing_output_files(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
                MODULE.main(["--offline", "--check", "--output-dir", str(output)])
            self.assertFalse((output / "plugins.json").exists())
            self.assertFalse((output / ".agents/plugins/marketplace.json").exists())

    def test_cli_subprocess_passes_command_line_arguments(self):
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_PATH),
                    "--offline",
                    "--output-dir",
                    folder,
                ],
                cwd=SCRIPT_PATH.parents[1],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((Path(folder) / "plugins.json").is_file())
            self.assertTrue((Path(folder) / ".agents/plugins/marketplace.json").is_file())


def only_path_resolves(path: str):
    """Fake `urlopen` where `path` answers 200 and every other path 404s."""

    def fake_urlopen(request, *_args, **_kwargs):
        if request.full_url.endswith(path):
            return FakeResponse(200)
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    return fake_urlopen


class FakeBody(FakeResponse):
    """A 200 response with a body, for files the generator reads."""

    def __init__(self, body: bytes) -> None:
        super().__init__(200)
        self.body = body

    def read(self, size: int = -1) -> bytes:
        return self.body if size < 0 else self.body[:size]


def serves(files: dict[str, bytes | None]):
    """Fake `urlopen` serving `files` by path suffix; a None body is HEAD-only."""

    def fake_urlopen(request, *_args, **_kwargs):
        url = request if isinstance(request, str) else request.full_url
        for path, body in files.items():
            if url.endswith("/HEAD/" + path):
                return FakeResponse(200) if body is None else FakeBody(body)
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    return fake_urlopen


def claude_marketplace_readme(directory: str, name: str) -> Path:
    readme = Path(directory) / "README.md"
    readme.write_text(
        "## Community Plugins\n\n"
        "### Development & Workflow\n\n"
        f"- [{name}](https://github.com/example/mods) - Claude Code plugin.\n"
    )
    return readme


class GeneratePluginsJsonTests(unittest.TestCase):
    def test_general_community_entries_do_not_assume_codex(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(
                "## Community Plugins\n\n"
                "### Development & Workflow\n\n"
                "- [Neutral Demo](https://github.com/example/neutral-demo)"
                " - A cross-client workflow tool.\n"
            )
            with patch.object(MODULE, "probe_install_url", return_value=None) as probe:
                plugins, added = MODULE.merge_readme_additions([], readme)

        self.assertEqual(added, 1)
        probe.assert_called_once()
        compatibility = MODULE.generate_plugins_json(plugins)
        marketplace = MODULE.generate_marketplace_json(
            [MODULE.marketplace_entry(plugin) for plugin in plugins]
        )
        for entry in (compatibility["plugins"][0], marketplace["plugins"][0]):
            self.assertNotIn("platform", entry)
            self.assertNotIn("ecosystems", entry)
            self.assertNotIn("install_url", entry)
            self.assertEqual(
                entry["installation_url"],
                "https://github.com/example/neutral-demo#readme",
            )
        self.assertNotIn("codex", compatibility["platforms"])
        self.assertNotIn("unknown", compatibility["platforms"])

    def test_codex_source_catalog_keeps_its_explicit_ecosystem(self) -> None:
        payload = {
            "plugins": [
                {
                    "name": "Codex Demo",
                    "owner": "example",
                    "repo": "codex-demo",
                    "url": "https://github.com/example/codex-demo",
                }
            ]
        }
        with patch.object(
            MODULE.urllib.request,
            "urlopen",
            return_value=FakeBody(json.dumps(payload).encode("utf-8")),
        ):
            plugins = MODULE.load_codex_plugins()

        self.assertEqual(plugins[0]["platform"], "codex")
        self.assertEqual(plugins[0]["ecosystems"], ["codex"])

    def test_native_subsection_does_not_add_codex_to_neutral_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(
                "## Community Plugins\n\n"
                "### Tools & Integrations\n\n"
                "- [Dual Listing](https://github.com/example/dual-listing)"
                " - A shared agent utility.\n\n"
                "### Grok Plugins\n\n"
                "- [Dual Listing](https://github.com/example/dual-listing)"
                " - A Grok extension.\n"
            )
            with patch.object(MODULE, "probe_install_url", return_value=None):
                plugins, added = MODULE.merge_readme_additions([], readme)

        self.assertEqual(added, 1)
        self.assertEqual(plugins[0]["platform"], "grok")
        self.assertEqual(plugins[0]["ecosystems"], ["grok"])
        self.assertNotIn("codex", plugins[0]["ecosystems"])

    def test_native_mcp_entry_has_documentation_in_both_feeds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(
                "## Community Plugins\n\n"
                "### Development & Workflow\n\n"
                "- [Native Demo](https://github.com/example/native-demo)"
                " - MCP server: A local stdio tool.\n"
            )
            with patch.object(MODULE, "probe_install_url") as probe:
                plugins, added = MODULE.merge_readme_additions([], readme)

            probe.assert_not_called()

        self.assertEqual(added, 1)
        compatibility = MODULE.generate_plugins_json(plugins)["plugins"][0]
        marketplace = MODULE.generate_marketplace_json(
            [MODULE.marketplace_entry(plugin) for plugin in plugins]
        )["plugins"][0]
        for entry in (compatibility, marketplace):
            self.assertEqual(entry["platform"], "mcp")
            self.assertEqual(entry["ecosystems"], ["mcp"])
            self.assertEqual(entry["category"], "Development & Workflow")
            self.assertEqual(
                entry["installation_url"],
                "https://github.com/example/native-demo#readme",
            )
            self.assertNotIn("install_url", entry)

    def test_native_agent_skills_use_source_installation_in_both_feeds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(
                "## Community Plugins\n\n### Agent Skills\n\n"
                "- [Native Skills](https://github.com/example/native-skills)"
                " - Installable Agent Skills collection.\n"
            )
            with patch.object(MODULE, "probe_install_url") as probe:
                plugins, added = MODULE.merge_readme_additions([], readme)
            probe.assert_not_called()

        self.assertEqual(added, 1)
        compatibility = MODULE.generate_plugins_json(plugins)["plugins"][0]
        marketplace = MODULE.generate_marketplace_json(
            [MODULE.marketplace_entry(plugin) for plugin in plugins]
        )["plugins"][0]
        for entry in (compatibility, marketplace):
            self.assertEqual(entry["platform"], "agent-skills")
            self.assertEqual(entry["ecosystems"], ["agent-skills"])
            self.assertEqual(entry["category"], "Agent Skills")
            self.assertEqual(
                entry["installation_url"],
                "https://github.com/example/native-skills#readme",
            )
            self.assertNotIn("install_url", entry)

    def test_native_agent_skills_merge_with_an_existing_plugin(self) -> None:
        install_url = (
            "https://raw.githubusercontent.com/example/shared/HEAD/"
            ".codex-plugin/plugin.json"
        )
        upstream = [{
            "name": "Shared Skills",
            "owner": "example",
            "repo": "shared-skills",
            "platform": "codex",
            "ecosystems": ["codex"],
            "install_url": install_url,
        }]
        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(
                "## Community Plugins\n\n### Agent Skills\n\n"
                "- [Shared Skills](https://github.com/example/shared-skills)"
                " - Native Agent Skills collection.\n"
            )
            with patch.object(MODULE, "probe_install_url") as probe:
                plugins, added = MODULE.merge_readme_additions(upstream, readme)
            probe.assert_not_called()

        self.assertEqual(added, 0)
        self.assertEqual(len(plugins), 1)
        self.assertEqual(plugins[0]["platform"], "codex")
        self.assertEqual(plugins[0]["install_url"], install_url)
        self.assertEqual(plugins[0]["ecosystems"], ["codex", "agent-skills"])
        self.assertEqual(
            plugins[0]["installation_url"],
            "https://github.com/example/shared-skills#readme",
        )

    def test_native_mcp_fallback_generates_no_invented_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            readme = root / "README.md"
            output = root / "plugins.json"
            marketplace = root / "marketplace.json"
            readme.write_text(
                "## MCP Servers (Cross-Platform)\n\n"
                "- [Native Demo](https://github.com/example/native-demo)"
                " - A local stdio tool.\n"
            )
            with (
                patch.object(MODULE, "README", readme),
                patch.object(MODULE, "OUTPUT", output),
                patch.object(MODULE, "MARKETPLACE_OUTPUT", marketplace),
                patch.object(MODULE, "load_codex_plugins", side_effect=OSError("offline")),
                patch.object(MODULE, "load_grok_plugins", return_value=[]),
                patch.object(MODULE, "probe_install_url") as probe,
            ):
                MODULE.main()

            probe.assert_not_called()
            for path in (output, marketplace):
                entry = json.loads(path.read_text())["plugins"][0]
                self.assertEqual(entry["platform"], "mcp")
                self.assertIn("installation_url", entry)
                self.assertNotIn("install_url", entry)

    def test_mcp_support_preserves_existing_codex_install(self) -> None:
        install_url = "https://raw.githubusercontent.com/example/shared/HEAD/.codex-plugin/plugin.json"
        upstream = [{
            "name": "Shared",
            "owner": "example",
            "repo": "shared",
            "platform": "codex",
            "ecosystems": ["codex"],
            "install_url": install_url,
        }]
        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(
                "## Community Plugins\n\n### Development & Workflow\n\n"
                "- [Shared](https://github.com/example/shared)"
                " - MCP server: Also provides native stdio tools.\n"
            )
            plugins, added = MODULE.merge_readme_additions(upstream, readme)

        self.assertEqual(added, 0)
        self.assertEqual(len(plugins), 1)
        self.assertEqual(plugins[0]["platform"], "codex")
        self.assertEqual(plugins[0]["install_url"], install_url)
        self.assertEqual(plugins[0]["ecosystems"], ["codex", "mcp"])
        self.assertEqual(
            plugins[0]["installation_url"], "https://github.com/example/shared#readme"
        )

    def test_mcp_words_alone_do_not_override_client_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(
                "## Community Plugins\n\n### Development & Workflow\n\n"
                "- [Wrapper](https://github.com/example/wrapper)"
                " - MCP server for a client plugin.\n"
            )
            with patch(
                "urllib.request.urlopen", only_path_resolves(".codex-plugin/plugin.json")
            ):
                plugins, added = MODULE.merge_readme_additions([], readme)

        self.assertEqual(added, 1)
        self.assertEqual(plugins[0]["platform"], "codex")
        self.assertIn("install_url", plugins[0])
        self.assertNotIn("installation_url", plugins[0])
    def test_standalone_tool_omits_plugin_install_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(
                "## Community Plugins\n\n### Tools & Integrations\n\n"
                "- [Demo](https://github.com/example/demo) - Native app."
                " <!-- platform: standalone -->\n"
            )
            with patch.object(MODULE, "probe_install_url") as probe:
                plugins, added = MODULE.merge_readme_additions([], readme)
            probe.assert_not_called()

        self.assertEqual(added, 1)
        self.assertEqual(plugins[0]["platform"], "standalone")
        self.assertEqual(plugins[0]["description"], "Native app")
        self.assertNotIn("install_url", plugins[0])
        entry = MODULE.marketplace_entry(plugins[0])
        self.assertEqual(entry["url"], "https://github.com/example/demo")
        self.assertNotIn("install_url", entry)

    def test_marketplace_timestamp_is_stable_for_the_day(self) -> None:
        marketplace = MODULE.generate_marketplace_json([])

        self.assertEqual(marketplace["last_updated"], date.today().isoformat())

    def test_merges_deepseek_ecosystem_into_existing_repository(self) -> None:
        upstream = [
            {
                "name": "Shared Plugin",
                "owner": "example",
                "repo": "shared-plugin",
                "platform": "codex",
                "ecosystems": ["codex"],
            }
        ]

        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(
                "## Community Plugins\n\n"
                "### DeepSeek Harness Plugins\n\n"
                "- [Shared Plugin](https://github.com/example/shared-plugin) - A shared plugin.\n"
            )

            plugins, added = MODULE.merge_readme_additions(upstream, readme)

        self.assertEqual(added, 0)
        self.assertEqual(len(plugins), 1)
        self.assertEqual(plugins[0]["ecosystems"], ["codex", "deepseek-harness"])

    def test_parses_native_grok_and_kimi_sections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(
                "## Community Plugins\n\n"
                "### Grok Plugins\n\n"
                "- [Grok Demo](https://github.com/example/grok-demo) - Grok plugin.\n\n"
                "### Kimi Plugins\n\n"
                "- [Kimi Demo](https://github.com/example/kimi-demo) - Kimi plugin.\n"
            )

            plugins = MODULE.parse_plugins(readme)

        self.assertEqual(
            [(plugin["name"], plugin["platform"]) for plugin in plugins],
            [("Grok Demo", "grok"), ("Kimi Demo", "kimi")],
        )
        self.assertEqual(
            plugins[0]["install_url"],
            "https://raw.githubusercontent.com/example/grok-demo/HEAD/.grok-plugin/plugin.json",
        )
        self.assertEqual(
            plugins[1]["install_url"],
            "https://raw.githubusercontent.com/example/kimi-demo/HEAD/kimi.plugin.json",
        )

    def test_merges_kimi_ecosystem_into_existing_repository(self) -> None:
        upstream = [
            {
                "name": "Shared Plugin",
                "owner": "example",
                "repo": "shared-plugin",
                "platform": "codex",
                "ecosystems": ["codex"],
            }
        ]

        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(
                "## Community Plugins\n\n"
                "### Kimi Plugins\n\n"
                "- [Shared Plugin](https://github.com/example/shared-plugin) - A shared plugin.\n"
            )

            plugins, added = MODULE.merge_readme_additions(upstream, readme)

        self.assertEqual(added, 0)
        self.assertEqual(plugins[0]["ecosystems"], ["codex", "kimi"])

    def test_omits_optional_grok_manifest_when_probe_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(
                "## Community Plugins\n\n"
                "### Grok Plugins\n\n"
                "- [Grok Demo](https://github.com/example/grok-demo) - Grok plugin.\n"
            )

            with patch.object(MODULE, "probe_install_url", return_value=None):
                plugins, added = MODULE.merge_readme_additions([], readme)

        self.assertEqual(added, 1)
        self.assertNotIn("install_url", plugins[0])


    def test_parses_native_opencode_section(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(
                "## Community Plugins\n\n"
                "### OpenCode Plugins\n\n"
                "- [RTL Demo](https://github.com/example/rtl-demo) - OpenCode plugin.\n"
            )

            plugins = MODULE.parse_plugins(readme)

        self.assertEqual(
            [(plugin["name"], plugin["platform"]) for plugin in plugins],
            [("RTL Demo", "opencode")],
        )
        self.assertNotIn("install_url", plugins[0])

    def test_opencode_entry_links_docs_without_manifest_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(
                "## Community Plugins\n\n"
                "### OpenCode Plugins\n\n"
                "- [RTL Demo](https://github.com/example/rtl-demo) - OpenCode plugin.\n"
            )
            with patch.object(MODULE, "probe_install_url") as probe:
                plugins, added = MODULE.merge_readme_additions([], readme)

            probe.assert_not_called()

        self.assertEqual(added, 1)
        compatibility = MODULE.generate_plugins_json(plugins)["plugins"][0]
        marketplace = MODULE.generate_marketplace_json(
            [MODULE.marketplace_entry(plugin) for plugin in plugins]
        )["plugins"][0]
        for entry in (compatibility, marketplace):
            self.assertEqual(entry["platform"], "opencode")
            self.assertEqual(entry["ecosystems"], ["opencode"])
            self.assertEqual(entry["category"], "OpenCode Plugins")
            self.assertEqual(entry["url"], "https://github.com/example/rtl-demo")
            self.assertEqual(
                entry["installation_url"],
                "https://github.com/example/rtl-demo#readme",
            )
            self.assertNotIn("install_url", entry)

    def test_detects_claude_code_manifest_in_mixed_community_section(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(
                "## Community Plugins\n\n"
                "### Development & Workflow\n\n"
                "- [Claude Demo](https://github.com/example/claude-demo)"
                " - Claude Code plugin.\n"
            )

            with patch(
                "urllib.request.urlopen",
                only_path_resolves(".claude-plugin/plugin.json"),
            ):
                plugins, added = MODULE.merge_readme_additions([], readme)

        self.assertEqual(added, 1)
        self.assertEqual(plugins[0]["platform"], "claude-code")
        self.assertEqual(plugins[0]["ecosystems"], ["claude-code"])
        self.assertEqual(
            plugins[0]["install_url"],
            "https://raw.githubusercontent.com/example/claude-demo"
            "/HEAD/.claude-plugin/plugin.json",
        )

    def test_keeps_codex_platform_for_codex_manifest_in_same_section(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(
                "## Community Plugins\n\n"
                "### Development & Workflow\n\n"
                "- [Codex Demo](https://github.com/example/codex-demo)"
                " - Codex plugin.\n"
            )

            with patch(
                "urllib.request.urlopen",
                only_path_resolves(".codex-plugin/plugin.json"),
            ):
                plugins, added = MODULE.merge_readme_additions([], readme)

        self.assertEqual(added, 1)
        self.assertEqual(plugins[0]["platform"], "codex")
        self.assertEqual(plugins[0]["ecosystems"], ["codex"])
        self.assertEqual(
            plugins[0]["install_url"],
            "https://raw.githubusercontent.com/example/codex-demo"
            "/HEAD/.codex-plugin/plugin.json",
        )

    def test_resolves_plugin_listed_in_claude_marketplace(self) -> None:
        marketplace = (
            b'{"name": "mods", "plugins": ['
            b'{"name": "other", "source": "./other"},'
            b'{"name": "token-bar", "source": "./token-bar"}]}'
        )
        with tempfile.TemporaryDirectory() as directory:
            readme = claude_marketplace_readme(directory, "token-bar")
            with patch(
                "urllib.request.urlopen",
                serves({
                    ".claude-plugin/marketplace.json": marketplace,
                    "token-bar/.claude-plugin/plugin.json": None,
                }),
            ):
                plugins, added = MODULE.merge_readme_additions([], readme)

        self.assertEqual(added, 1)
        self.assertEqual(plugins[0]["platform"], "claude-code")
        self.assertEqual(plugins[0]["ecosystems"], ["claude-code"])
        self.assertEqual(
            plugins[0]["install_url"],
            "https://raw.githubusercontent.com/example/mods"
            "/HEAD/token-bar/.claude-plugin/plugin.json",
        )

    def test_single_plugin_marketplace_resolves_under_another_name(self) -> None:
        marketplace = b'{"plugins": [{"name": "bar", "source": "plugins/bar/"}]}'
        with patch(
            "urllib.request.urlopen",
            serves({
                ".claude-plugin/marketplace.json": marketplace,
                "plugins/bar/.claude-plugin/plugin.json": None,
            }),
        ):
            found, url = MODULE.probe_claude_marketplace("example", "mods", "Bar Plugin")

        self.assertTrue(found)
        self.assertEqual(
            url,
            "https://raw.githubusercontent.com/example/mods"
            "/HEAD/plugins/bar/.claude-plugin/plugin.json",
        )

    def test_ignores_marketplace_sources_outside_the_repo(self) -> None:
        for source in ('"../elsewhere"', '"https://example.com/x"', '{"source": "github", "repo": "a/b"}'):
            marketplace = ('{"plugins": [{"name": "x", "source": %s}]}' % source).encode()
            with self.subTest(source=source), patch(
                "urllib.request.urlopen",
                serves({
                    ".claude-plugin/marketplace.json": marketplace,
                    "elsewhere/.claude-plugin/plugin.json": None,
                }),
            ):
                self.assertEqual(MODULE.probe_claude_marketplace("example", "mods", "x"), (True, None))

    def test_ignores_marketplace_whose_plugin_manifest_is_missing(self) -> None:
        marketplace = b'{"plugins": [{"name": "x", "source": "./x"}]}'
        with patch(
            "urllib.request.urlopen",
            serves({".claude-plugin/marketplace.json": marketplace}),
        ):
            self.assertEqual(MODULE.probe_claude_marketplace("example", "mods", "x"), (True, None))

    def test_no_marketplace_is_not_found(self) -> None:
        with patch("urllib.request.urlopen", serves({})):
            self.assertEqual(MODULE.probe_claude_marketplace("example", "mods", "x"), (False, None))

    def test_unresolved_marketplace_has_no_install_target(self) -> None:
        marketplace = (
            b'{"plugins": [{"name": "a", "source": "./a"}, {"name": "b", "source": "./b"}]}'
        )
        with tempfile.TemporaryDirectory() as directory:
            readme = claude_marketplace_readme(directory, "token-bar")
            with patch(
                "urllib.request.urlopen",
                serves({
                    ".claude-plugin/marketplace.json": marketplace,
                    "a/.claude-plugin/plugin.json": None,
                }),
            ):
                plugins, _ = MODULE.merge_readme_additions([], readme)

        # The root manifests are missing, so a default path would 404 or name another bundle.
        self.assertNotIn("install_url", plugins[0])
        self.assertEqual(plugins[0]["platform"], "claude-code")
        self.assertNotIn("install_url", MODULE.marketplace_entry(plugins[0]))

    def test_unreadable_marketplace_has_no_install_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            readme = claude_marketplace_readme(directory, "token-bar")
            with patch(
                "urllib.request.urlopen",
                serves({".claude-plugin/marketplace.json": b"not json"}),
            ):
                plugins, _ = MODULE.merge_readme_additions([], readme)

        self.assertNotIn("install_url", plugins[0])
        self.assertEqual(plugins[0]["platform"], "claude-code")


if __name__ == "__main__":
    unittest.main()
