from __future__ import annotations

import importlib.util
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
