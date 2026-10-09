from __future__ import annotations

import importlib.util
import json
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


def only_path_resolves(path: str):
    """Fake `urlopen` where `path` answers 200 and every other path 404s."""

    def fake_urlopen(request, *_args, **_kwargs):
        if request.full_url.endswith(path):
            return FakeResponse(200)
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    return fake_urlopen


class GeneratePluginsJsonTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
