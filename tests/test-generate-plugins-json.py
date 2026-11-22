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


    def test_require_upstreams_rejects_offline_mode(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            MODULE.main(["--offline", "--require-upstreams"])

    def test_require_upstreams_codex_failure_leaves_outputs_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old = root / "plugins.json"
            old.write_text('{"sentinel": true}\\n')
            with (
                patch.object(MODULE, "load_codex_plugins", side_effect=OSError("network unavailable")),
                patch.object(MODULE, "load_grok_plugins") as grok,
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaisesRegex(RuntimeError, "Codex upstream feed unavailable"),
            ):
                MODULE.main(["--require-upstreams", "--output-dir", folder])
            grok.assert_not_called()
            self.assertEqual(old.read_text(), '{"sentinel": true}\\n')
            self.assertFalse((root / ".agents/plugins/marketplace.json").exists())

    def test_require_upstreams_missing_grok_leaves_outputs_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with (
                patch.object(MODULE, "load_codex_plugins", return_value=[{"name": "Codex Demo"}]),
                patch.object(MODULE, "load_grok_plugins", return_value=[]),
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaisesRegex(RuntimeError, "Grok upstream feed is unavailable or empty"),
            ):
                MODULE.main(["--require-upstreams", "--output-dir", folder])
            self.assertFalse((root / "plugins.json").exists())
            self.assertFalse((root / ".agents/plugins/marketplace.json").exists())

    def test_grok_feed_does_not_claim_unrelated_claude_bundles(self):
        # A shared GitHub repo is not proof that every independently named
        # Claude bundle implements every Grok plugin from that repository.
        codex = [
            {
                "name": name, "owner": "example", "repo": "shared",
                "platform": "claude-code", "ecosystems": ["claude-code"],
                "install_url": f"https://example.test/{slug}/plugin.json",
            }
            for name, slug in (("Sidequest", "sidequest"),
                               ("Quality Gate", "quality-gate"))
        ]
        grok = [
            {"name": "Unrelated Grok", "owner": "example", "repo": "shared",
             "platform": "grok", "ecosystems": ["grok"]},
            {"name": "Another Grok", "owner": "example", "repo": "shared",
             "platform": "grok", "ecosystems": ["grok"]},
        ]
        with tempfile.TemporaryDirectory() as folder:
            with (
                patch.object(MODULE, "load_codex_plugins", return_value=codex),
                patch.object(MODULE, "load_grok_plugins", return_value=grok),
                patch.object(
                    MODULE, "merge_readme_additions",
                    side_effect=lambda plugins, _readme, **_kwargs: (plugins, 0),
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                MODULE.main(["--output-dir", folder])
            plugins = json.loads(
                (Path(folder) / "plugins.json").read_text()
            )["plugins"]
        self.assertEqual(len(plugins), 4)
        self.assertEqual([p["name"] for p in plugins], [
            "Sidequest", "Quality Gate", "Unrelated Grok", "Another Grok",
        ])
        self.assertEqual(
            [p["ecosystems"] for p in plugins],
            [["claude-code"], ["claude-code"], ["grok"], ["grok"]],
        )

    def test_grok_feed_matches_one_real_named_bundle_only(self):
        codex = [
            {"name": name, "owner": "example", "repo": "shared",
             "platform": "claude-code", "ecosystems": ["claude-code"]}
            for name in ("Sidequest", "Quality Gate")
        ]
        grok = [
            {"name": "Quality Gate", "owner": "example", "repo": "shared",
             "platform": "grok", "ecosystems": ["grok"]},
            {"name": "New Grok", "owner": "example", "repo": "shared",
             "platform": "grok", "ecosystems": ["grok"]},
        ]
        with tempfile.TemporaryDirectory() as folder:
            with (
                patch.object(MODULE, "load_codex_plugins", return_value=codex),
                patch.object(MODULE, "load_grok_plugins", return_value=grok),
                patch.object(
                    MODULE, "merge_readme_additions",
                    side_effect=lambda plugins, _readme, **_kwargs: (plugins, 0),
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                MODULE.main(["--output-dir", folder])
            plugins = json.loads(
                (Path(folder) / "plugins.json").read_text()
            )["plugins"]
        self.assertEqual([p["name"] for p in plugins], [
            "Sidequest", "Quality Gate", "New Grok",
        ])
        self.assertEqual([p["ecosystems"] for p in plugins], [
            ["claude-code"], ["claude-code", "grok"], ["grok"],
        ])

    def test_single_unrelated_grok_entry_is_not_merged_into_claude(self):
        # A single existing bundle is not proof that an unrelated Grok
        # bundle in the same repository provides the Claude runtime.
        claude = {
            "name": "Sidequest", "owner": "example", "repo": "shared",
            "platform": "claude-code", "ecosystems": ["claude-code"],
        }
        grok = {
            "name": "Search Agent", "owner": "example", "repo": "shared",
            "platform": "grok", "ecosystems": ["grok"],
        }
        with tempfile.TemporaryDirectory() as folder:
            with (
                patch.object(MODULE, "load_codex_plugins", return_value=[claude]),
                patch.object(MODULE, "load_grok_plugins", return_value=[grok]),
                patch.object(
                    MODULE, "merge_readme_additions",
                    side_effect=lambda plugins, _readme, **_kwargs: (plugins, 0),
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                MODULE.main(["--output-dir", folder])
            entries = json.loads(
                (Path(folder) / "plugins.json").read_text()
            )["plugins"]
        self.assertEqual([p["name"] for p in entries], ["Sidequest", "Search Agent"])
        self.assertEqual(
            [p["ecosystems"] for p in entries], [["claude-code"], ["grok"]]
        )

    def test_single_matching_grok_entry_joins_existing_bundle(self):
        # The exact same independently named source bundle still combines
        # runtime support without publishing a duplicate plugin identity.
        codex = {
            "name": "Sidequest", "owner": "example", "repo": "shared",
            "platform": "codex", "ecosystems": ["codex"],
        }
        grok = {
            "name": "Sidequest", "owner": "example", "repo": "shared",
            "platform": "grok", "ecosystems": ["grok"],
        }
        with tempfile.TemporaryDirectory() as folder:
            with (
                patch.object(MODULE, "load_codex_plugins", return_value=[codex]),
                patch.object(MODULE, "load_grok_plugins", return_value=[grok]),
                patch.object(
                    MODULE, "merge_readme_additions",
                    side_effect=lambda plugins, _readme, **_kwargs: (plugins, 0),
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                MODULE.main(["--output-dir", folder])
            entries = json.loads(
                (Path(folder) / "plugins.json").read_text()
            )["plugins"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["ecosystems"], ["codex", "grok"])

    def test_require_upstreams_with_both_sources_writes_both_feeds(self):
        codex = {"name": "Codex Demo", "owner": "test", "repo": "codex"}
        grok = {"name": "Grok Demo", "owner": "test", "repo": "grok"}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with (
                patch.object(MODULE, "load_codex_plugins", return_value=[codex]),
                patch.object(MODULE, "load_grok_plugins", return_value=[grok]),
                patch.object(MODULE, "merge_readme_additions", return_value=([codex, grok], 0)),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                MODULE.main(["--require-upstreams", "--output-dir", folder])
            self.assertEqual(json.loads((root / "plugins.json").read_text())["total"], 2)
            self.assertEqual(len(json.loads((root / ".agents/plugins/marketplace.json").read_text())["plugins"]), 2)


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
    def test_manifest_probe_continues_after_one_candidate_network_failure(self) -> None:
        # A timeout on an earlier manifest location must not suppress a
        # separately verified later candidate for the same repository.
        observed = []

        def probe(request, *_args, **_kwargs):
            observed.append(request.full_url)
            if request.full_url.endswith("/first/plugin.json"):
                raise urllib.error.URLError("first location temporarily unavailable")
            if request.full_url.endswith("/second/plugin.json"):
                return FakeResponse(200)
            raise AssertionError(f"unexpected manifest path: {request.full_url}")

        with patch("urllib.request.urlopen", side_effect=probe):
            result = MODULE.probe_install_url(
                "example", "mods", ("first/plugin.json", "second/plugin.json")
            )

        self.assertEqual(
            result,
            "https://raw.githubusercontent.com/example/mods/HEAD/second/plugin.json",
        )
        self.assertEqual(len(observed), 2)

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

    def test_native_duplicate_never_probes_unrelated_claude_bundle(self) -> None:
        for category, description, platform in (
            ("Agent Skills", "Native Agent Skills collection.", "agent-skills"),
            ("Development & Workflow", "MCP server: Native stdio tools.", "mcp"),
        ):
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as directory:
                readme = Path(directory) / "README.md"
                readme.write_text(
                    "## Community Plugins\n"
                    f"### {category}\n"
                    f"- [Shared](https://github.com/example/shared) - {description}\n"
                )
                upstream = [{
                    "name": "Shared",
                    "owner": "example",
                    "repo": "shared",
                    "platform": "codex",
                    "ecosystems": ["codex"],
                    "install_url": "https://raw.githubusercontent.com/example/shared/HEAD/.codex-plugin/plugin.json",
                }]
                with patch.object(
                    MODULE, "probe_claude_marketplace",
                    side_effect=AssertionError("native duplicate must not trigger Claude probe"),
                ):
                    plugins, added = MODULE.merge_readme_additions(upstream, readme)
                self.assertEqual(added, 0)
                self.assertEqual(len(plugins), 1)
                self.assertEqual(plugins[0]["ecosystems"], ["codex", platform])

    def test_native_mcp_stays_separate_from_multiple_claude_bundles(self) -> None:
        # A repository can contain two separately installable Claude plugins
        # plus its own MCP server. The native entry must not inherit the first
        # unrelated Claude bundle's platform, name, or manifest install URL.
        upstream = [
            {
                "name": name,
                "owner": "example",
                "repo": "shared",
                "platform": "claude-code",
                "ecosystems": ["claude-code"],
                "install_url": (
                    "https://raw.githubusercontent.com/example/shared/HEAD/"
                    f"plugins/{slug}/.claude-plugin/plugin.json"
                ),
            }
            for name, slug in (
                ("Sidequest", "sidequest"),
                ("Quality Gate", "quality-gate"),
            )
        ]
        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(
                "## Community Plugins\n"
                "### Development & Workflow\n"
                "- [Native Tools](https://github.com/example/shared)"
                " - MCP server: Native stdio tools.\n"
            )
            with patch.object(
                MODULE, "probe_claude_marketplace",
                side_effect=AssertionError("native MCP entry must not probe Claude"),
            ):
                plugins, added = MODULE.merge_readme_additions(upstream, readme)

        self.assertEqual(added, 1)
        self.assertEqual(len(plugins), 3)
        self.assertEqual([p["platform"] for p in plugins], [
            "claude-code", "claude-code", "mcp",
        ])
        self.assertEqual([p["ecosystems"] for p in plugins[:2]], [
            ["claude-code"], ["claude-code"],
        ])
        self.assertEqual(plugins[2]["name"], "Native Tools")
        self.assertIn("installation_url", plugins[2])
        self.assertNotIn("install_url", plugins[2])

    def test_same_named_native_bundle_targets_matching_claude_manifest(self) -> None:
        # When a native integration genuinely shares a display identity with
        # one of two Claude bundles, merge only into that named bundle.
        upstream = [
            {
                "name": name,
                "owner": "example",
                "repo": "shared",
                "platform": "claude-code",
                "ecosystems": ["claude-code"],
                "install_url": (
                    "https://raw.githubusercontent.com/example/shared/HEAD/"
                    f"plugins/{slug}/.claude-plugin/plugin.json"
                ),
            }
            for name, slug in (
                ("Sidequest", "sidequest"),
                ("Quality Gate", "quality-gate"),
            )
        ]
        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(
                "## Community Plugins\n"
                "### Development & Workflow\n"
                "- [Quality Gate](https://github.com/example/shared)"
                " - MCP server: Same named native runtime.\n"
            )
            with patch.object(
                MODULE, "probe_claude_marketplace",
                side_effect=AssertionError("native MCP must not probe Claude"),
            ):
                plugins, added = MODULE.merge_readme_additions(upstream, readme)
        self.assertEqual(added, 0)
        self.assertEqual(len(plugins), 2)
        self.assertEqual(plugins[0]["ecosystems"], ["claude-code"])
        self.assertEqual(plugins[1]["ecosystems"], ["claude-code", "mcp"])
        self.assertEqual(plugins[1]["platform"], "claude-code")
        self.assertIn("installation_url", plugins[1])
        self.assertNotIn("installation_url", plugins[0])

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

    def test_distinct_named_claude_plugins_share_repository(self) -> None:
        marketplace = (
            b'{"name":"eigenwise-toolshed","plugins":['
            b'{"name":"sidequest","source":"./plugins/sidequest"},'
            b'{"name":"quality-gate","source":"./plugins/quality-gate"}]}'
        )
        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(
                "## Community Plugins\n"
                "### Development & Workflow\n"
                "- [sidequest](https://github.com/Eigenwise/eigenwise-toolshed) - Tasks.\n"
                "- [quality-gate](https://github.com/Eigenwise/eigenwise-toolshed) - Tests.\n"
            )
            with patch(
                "urllib.request.urlopen",
                serves({
                    ".claude-plugin/marketplace.json": marketplace,
                    "plugins/sidequest/.claude-plugin/plugin.json": None,
                    "plugins/quality-gate/.claude-plugin/plugin.json": None,
                }),
            ):
                plugins, added = MODULE.merge_readme_additions([], readme)
        self.assertEqual(added, 2)
        self.assertEqual([p["name"] for p in plugins], ["sidequest", "quality-gate"])
        self.assertEqual({p["platform"] for p in plugins}, {"claude-code"})
        self.assertEqual(len({p["install_url"] for p in plugins}), 2)
        marketplace_entries = [
            MODULE.marketplace_entry(plugin) for plugin in plugins
        ]
        self.assertEqual(
            [p["name"] for p in marketplace_entries], ["sidequest", "quality-gate"]
        )

    def test_seven_eigenwise_bundles_keep_distinct_manifests(self) -> None:
        # Current upstream Toolshed publishes seven separately named Claude
        # bundles from one repo. No first-bundle aliasing is permissible.
        names = ["observability","model-gateway","sidequest","codebase-mapper","quartermaster","quality-gate","live-rules"]
        marketplace = json.dumps({
            "plugins": [
                {"name": name, "source": f"./plugins/{name}"}
                for name in names
            ],
        }).encode("utf-8")
        manifests = {
            f"plugins/{name}/.claude-plugin/plugin.json": None
            for name in names
        }
        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(
                "## Community Plugins\n### Development & Workflow\n"
                + "".join(
                    f"- [{name}](https://github.com/Eigenwise/eigenwise-toolshed) - Plugin.\n"
                    for name in names
                ),
                encoding="utf-8",
            )
            with patch(
                "urllib.request.urlopen",
                side_effect=serves({
                    ".claude-plugin/marketplace.json": marketplace,
                    **manifests,
                }),
            ):
                plugins, added = MODULE.merge_readme_additions([], readme)
        self.assertEqual(added, 7)
        self.assertEqual([p["name"] for p in plugins], names)
        urls = [p["install_url"] for p in plugins]
        self.assertEqual(len(set(urls)), 7)
        for name, url in zip(names, urls):
            self.assertEqual(
                url,
                "https://raw.githubusercontent.com/Eigenwise/eigenwise-toolshed"
                f"/HEAD/plugins/{name}/.claude-plugin/plugin.json",
            )
        self.assertEqual(
            {p["platform"] for p in plugins}, {"claude-code"}
        )

    def test_shared_repo_first_bundle_prefers_named_marketplace_over_root(self) -> None:
        # An unrelated root plugin.json must not become the install target of
        # the first named bundle merely because the generic HEAD probe wins.
        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(
                "## Community Plugins\n### Development & Workflow\n"
                "- [Sidequest](https://github.com/example/shared) - Tasks.\n"
                "- [Quality Gate](https://github.com/example/shared) - Tests.\n"
            )
            def named_probe(owner, repo, name, *, require_named_match=False):
                self.assertTrue(require_named_match)
                slug = {"Sidequest": "sidequest", "Quality Gate": "quality-gate"}[name]
                return True, (
                    "https://raw.githubusercontent.com/example/shared/HEAD/"
                    f"plugins/{slug}/.claude-plugin/plugin.json"
                )
            with (
                patch.object(MODULE, "probe_claude_marketplace", side_effect=named_probe),
                patch.object(
                    MODULE, "probe_install_url",
                    return_value="https://raw.githubusercontent.com/example/shared/HEAD/"
                                 ".claude-plugin/plugin.json",
                ) as root_probe,
            ):
                plugins, added = MODULE.merge_readme_additions([], readme)
            root_probe.assert_not_called()
        self.assertEqual(added, 2)
        self.assertEqual([p["name"] for p in plugins], ["Sidequest", "Quality Gate"])
        self.assertEqual({p["platform"] for p in plugins}, {"claude-code"})
        self.assertEqual(
            [p["install_url"].split("/HEAD/")[1] for p in plugins],
            ["plugins/sidequest/.claude-plugin/plugin.json",
             "plugins/quality-gate/.claude-plugin/plugin.json"],
        )

    def test_unavailable_multi_plugin_marketplace_never_claims_root_manifest(self) -> None:
        # A transient source error is not evidence that the first named row
        # points at an unrelated root manifest in the same repository.
        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(
                "## Community Plugins\n### Development & Workflow\n"
                "- [Sidequest](https://github.com/example/shared) - Tasks.\n"
                "- [Quality Gate](https://github.com/example/shared) - Tests.\n"
            )
            with (
                patch.object(
                    MODULE, "probe_claude_marketplace", return_value=(False, None)
                ) as source_probe,
                patch.object(
                    MODULE, "probe_install_url",
                    return_value="https://raw.githubusercontent.com/example/shared/"
                                 "HEAD/.codex-plugin/plugin.json",
                ) as root_probe,
            ):
                plugins, added = MODULE.merge_readme_additions([], readme)

        self.assertEqual(added, 0)
        self.assertEqual(plugins, [])
        self.assertEqual(source_probe.call_count, 2)
        root_probe.assert_not_called()

    def test_unverified_first_named_bundle_does_not_alias_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(
                "## Community Plugins\n### Development & Workflow\n"
                "- [Unlisted](https://github.com/example/shared) - Unknown.\n"
                "- [Sidequest](https://github.com/example/shared) - Tasks.\n"
            )
            def named_probe(owner, repo, name, *, require_named_match=False):
                self.assertTrue(require_named_match)
                if name == "Unlisted":
                    return True, None
                return True, (
                    "https://raw.githubusercontent.com/example/shared/HEAD/"
                    "plugins/sidequest/.claude-plugin/plugin.json"
                )
            with (
                patch.object(MODULE, "probe_claude_marketplace", side_effect=named_probe),
                patch.object(MODULE, "probe_install_url") as root_probe,
            ):
                plugins, added = MODULE.merge_readme_additions([], readme)
            root_probe.assert_not_called()
        self.assertEqual(added, 1)
        self.assertEqual([p["name"] for p in plugins], ["Sidequest"])

    def test_readable_names_match_exact_unique_marketplace_slugs(self) -> None:
        # Real Claude marketplaces use slug names while README catalog rows
        # display human-readable labels. Both must resolve distinct manifests.
        marketplace = (
            b'{"plugins":['
            b'{"name":"codebase-mapper","source":"./plugins/codebase-mapper"},'
            b'{"name":"quality-gate","source":"./plugins/quality-gate"}]}'
        )
        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(
                "## Community Plugins\n"
                "### Development & Workflow\n"
                "- [Codebase Mapper](https://github.com/Eigenwise/eigenwise-toolshed) - Project map.\n"
                "- [Quality Gate](https://github.com/Eigenwise/eigenwise-toolshed) - Code checks.\n"
            )
            with patch(
                "urllib.request.urlopen",
                serves({
                    ".claude-plugin/marketplace.json": marketplace,
                    "plugins/codebase-mapper/.claude-plugin/plugin.json": None,
                    "plugins/quality-gate/.claude-plugin/plugin.json": None,
                }),
            ):
                plugins, added = MODULE.merge_readme_additions([], readme)
        self.assertEqual(added, 2)
        self.assertEqual([p["name"] for p in plugins], ["Codebase Mapper", "Quality Gate"])
        self.assertTrue(all(p.get("install_url") for p in plugins))
        self.assertEqual(len({p["install_url"] for p in plugins}), 2)

    def test_readable_aliases_deduplicate_one_verified_manifest(self) -> None:
        # Display labels and source slugs must not become two catalog entries
        # for the same installable bundle in a shared marketplace.
        marketplace = b'{"plugins":[{"name":"codebase-mapper","source":"./plugins/codebase-mapper"}]}'
        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(
                "## Community Plugins\n"
                "### Development & Workflow\n"
                "- [Codebase Mapper](https://github.com/Eigenwise/eigenwise-toolshed) - Project map.\n"
                "- [codebase-mapper](https://github.com/Eigenwise/eigenwise-toolshed) - Alias.\n"
            )
            with patch(
                "urllib.request.urlopen",
                serves({
                    ".claude-plugin/marketplace.json": marketplace,
                    "plugins/codebase-mapper/.claude-plugin/plugin.json": None,
                }),
            ):
                plugins, added = MODULE.merge_readme_additions([], readme)

        self.assertEqual(added, 1)
        self.assertEqual([p["name"] for p in plugins], ["Codebase Mapper"])
        self.assertTrue(plugins[0].get("install_url"))

    def test_same_name_codex_upstream_and_claude_bundle_are_distinct(self) -> None:
        # A Codex feed entry must not suppress a separately installable Claude
        # plugin that shares its repository and display name.
        marketplace = (
            b'{"plugins":[{"name":"sidequest","source":"./plugins/sidequest"}]}'
        )
        upstream = [{
            "name": "sidequest",
            "owner": "Eigenwise",
            "repo": "eigenwise-toolshed",
            "url": "https://github.com/Eigenwise/eigenwise-toolshed",
            "platform": "codex",
            "ecosystems": ["codex"],
            "install_url": (
                "https://raw.githubusercontent.com/Eigenwise/eigenwise-toolshed"
                "/HEAD/.codex-plugin/plugin.json"
            ),
        }]
        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(
                "## Community Plugins\n"
                "### Development & Workflow\n"
                "- [sidequest](https://github.com/Eigenwise/eigenwise-toolshed) - Claude plugin.\n"
            )
            with patch(
                "urllib.request.urlopen",
                serves({
                    ".claude-plugin/marketplace.json": marketplace,
                    "plugins/sidequest/.claude-plugin/plugin.json": None,
                }),
            ):
                plugins, added = MODULE.merge_readme_additions(upstream, readme)

        self.assertEqual(added, 1)
        self.assertEqual(len(plugins), 2)
        self.assertEqual([p["platform"] for p in plugins], ["codex", "claude-code"])
        self.assertEqual(len({p["install_url"] for p in plugins}), 2)
        self.assertEqual([p["name"] for p in plugins], ["sidequest", "sidequest"])

    def test_existing_upstream_bundle_and_distinct_nested_bundle(self) -> None:
        # A Codex/upstream feed may already list one real Claude marketplace
        # bundle. Another named manifest from that repository still qualifies.
        marketplace = (
            b'{"name":"eigenwise-toolshed","plugins":['
            b'{"name":"sidequest","source":"./plugins/sidequest"},'
            b'{"name":"quality-gate","source":"./plugins/quality-gate"}]}'
        )
        upstream = [{
            "name": "sidequest",
            "owner": "Eigenwise",
            "repo": "eigenwise-toolshed",
            "url": "https://github.com/Eigenwise/eigenwise-toolshed",
            "platform": "claude-code",
            "ecosystems": ["claude-code"],
            "install_url": (
                "https://raw.githubusercontent.com/Eigenwise/eigenwise-toolshed"
                "/HEAD/plugins/sidequest/.claude-plugin/plugin.json"
            ),
        }]
        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(
                "## Community Plugins\n"
                "### Development & Workflow\n"
                "- [sidequest](https://github.com/Eigenwise/eigenwise-toolshed) - Tasks.\n"
                "- [quality-gate](https://github.com/Eigenwise/eigenwise-toolshed) - Tests.\n"
            )
            with patch(
                "urllib.request.urlopen",
                serves({
                    ".claude-plugin/marketplace.json": marketplace,
                    "plugins/sidequest/.claude-plugin/plugin.json": None,
                    "plugins/quality-gate/.claude-plugin/plugin.json": None,
                }),
            ):
                plugins, added = MODULE.merge_readme_additions(upstream, readme)

        self.assertEqual(added, 1)
        self.assertEqual([p["name"] for p in plugins], ["sidequest", "quality-gate"])
        self.assertEqual(len({p["install_url"] for p in plugins}), 2)
        self.assertEqual({p["platform"] for p in plugins}, {"claude-code"})

    def test_ambiguous_marketplace_name_matches_fail_closed(self) -> None:
        # Marketplace metadata may contain duplicate aliases. An exact
        # case-insensitive match or slug-normalized match is not enough when
        # two independent source directories claim the same name.
        scenarios = (
            (
                "sidequest",
                [{"name": "SideQuest", "source": "./plugins/a"},
                 {"name": "sidequest", "source": "./plugins/b"}],
            ),
            (
                "Code Base",
                [{"name": "code_base", "source": "./plugins/a"},
                 {"name": "code-base", "source": "./plugins/b"}],
            ),
        )
        for name, entries in scenarios:
            with self.subTest(name=name), patch(
                "urllib.request.urlopen",
                side_effect=serves({
                    ".claude-plugin/marketplace.json": json.dumps(
                        {"plugins": entries}
                    ).encode("utf-8"),
                }),
            ) as urlopen:
                self.assertEqual(
                    MODULE.probe_claude_marketplace(
                        "example", "mods", name, require_named_match=True
                    ),
                    (True, None),
                )
                # Never make an install-manifest request for an ambiguous name.
                self.assertEqual(urlopen.call_count, 1)

    def test_same_manifest_cannot_be_published_under_two_names(self) -> None:
        marketplace = b'{"plugins":[{"name":"sidequest","source":"./plugins/sidequest"}]}'
        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(
                "## Community Plugins\n"
                "### Development & Workflow\n"
                "- [sidequest](https://github.com/Eigenwise/eigenwise-toolshed) - Tasks.\n"
                "- [another name](https://github.com/Eigenwise/eigenwise-toolshed) - Same.\n"
            )
            with patch(
                "urllib.request.urlopen",
                serves({
                    ".claude-plugin/marketplace.json": marketplace,
                    "plugins/sidequest/.claude-plugin/plugin.json": None,
                }),
            ):
                plugins, added = MODULE.merge_readme_additions([], readme)
        self.assertEqual(added, 1)
        self.assertEqual([p["name"] for p in plugins], ["sidequest"])

    def test_unverified_second_marketplace_name_remains_excluded(self) -> None:
        marketplace = (
            b'{"plugins":[{"name":"sidequest","source":"./plugins/sidequest"},'
            b'{"name":"quality-gate","source":"./plugins/quality-gate"}]}'
        )
        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(
                "## Community Plugins\n"
                "### Development & Workflow\n"
                "- [sidequest](https://github.com/Eigenwise/eigenwise-toolshed) - Tasks.\n"
                "- [not-a-plugin](https://github.com/Eigenwise/eigenwise-toolshed) - Unknown.\n"
            )
            with patch(
                "urllib.request.urlopen",
                serves({
                    ".claude-plugin/marketplace.json": marketplace,
                    "plugins/sidequest/.claude-plugin/plugin.json": None,
                }),
            ):
                plugins, added = MODULE.merge_readme_additions([], readme)
        self.assertEqual(added, 1)
        self.assertEqual([p["name"] for p in plugins], ["sidequest"])

    def test_existing_repo_cannot_publish_unmatched_single_marketplace_alias(self) -> None:
        # A README alias is not source evidence for a *second* installable
        # plugin when the repository already has a catalog entry.
        marketplace = b'{"plugins":[{"name":"actual-plugin","source":"./plugins/actual-plugin"}]}'
        upstream = [{
            "name": "Existing Codex integration",
            "owner": "example", "repo": "mods",
            "platform": "codex", "ecosystems": ["codex"],
            "install_url": "https://raw.githubusercontent.com/example/mods/HEAD/.codex-plugin/plugin.json",
        }]
        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(
                "## Community Plugins\n"
                "### Development & Workflow\n"
                "- [Invented alias](https://github.com/example/mods) - An unverified second name.\n"
            )
            with patch(
                "urllib.request.urlopen",
                side_effect=serves({
                    ".claude-plugin/marketplace.json": marketplace,
                    "plugins/actual-plugin/.claude-plugin/plugin.json": None,
                }),
            ) as urlopen:
                plugins, added = MODULE.merge_readme_additions(upstream, readme)
        self.assertEqual(added, 0)
        self.assertEqual(len(plugins), 1)
        self.assertEqual(plugins[0]["name"], "Existing Codex integration")
        # The unverified alias cannot even trigger a manifest acceptance HEAD.
        self.assertEqual(urlopen.call_count, 1)

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

    def test_malformed_extra_marketplace_entry_is_not_singleton(self) -> None:
        # A malformed second record cannot turn a multi-entry marketplace
        # into a single plugin whose manifest is misattributed to a README alias.
        marketplace = b'{"plugins":[{"name":"bar","source":"plugins/bar"},"invalid"]}'
        with patch(
            "urllib.request.urlopen",
            side_effect=serves({
                ".claude-plugin/marketplace.json": marketplace,
                "plugins/bar/.claude-plugin/plugin.json": None,
            }),
        ) as urlopen:
            found, url = MODULE.probe_claude_marketplace(
                "example", "mods", "Invented Alias"
            )
        self.assertTrue(found)
        self.assertIsNone(url)
        self.assertEqual(urlopen.call_count, 1)

    def test_blank_marketplace_source_never_uses_root_manifest(self) -> None:
        # Empty or whitespace-only source must not accidentally claim a
        # repository-root plugin as the named marketplace bundle.
        for source in ("", "   "):
            with self.subTest(source=source):
                marketplace = json.dumps({
                    "plugins": [{"name": "x", "source": source}]
                }).encode("utf-8")
                with patch(
                    "urllib.request.urlopen",
                    side_effect=serves({
                        ".claude-plugin/marketplace.json": marketplace,
                        ".claude-plugin/plugin.json": None,
                    }),
                ) as urlopen:
                    self.assertEqual(
                        MODULE.probe_claude_marketplace("example", "mods", "x"),
                        (True, None),
                    )
                    self.assertEqual(urlopen.call_count, 1)

    def test_ignores_marketplace_sources_outside_the_repo(self) -> None:
        # Rooted paths must not be converted to repository-relative paths;
        # a matching local manifest in the test would otherwise be accepted.
        for source in ('"../elsewhere"', '"/elsewhere"', '"https://example.com/x"', '{"source": "github", "repo": "a/b"}'):
            marketplace = ('{"plugins": [{"name": "x", "source": %s}]}' % source).encode()
            with self.subTest(source=source), patch(
                "urllib.request.urlopen",
                serves({
                    ".claude-plugin/marketplace.json": marketplace,
                    "elsewhere/.claude-plugin/plugin.json": None,
                }),
            ):
                self.assertEqual(MODULE.probe_claude_marketplace("example", "mods", "x"), (True, None))

    def test_rejects_windows_drive_and_stream_marketplace_sources(self) -> None:
        # Source directories must be portable, not drive-qualified or NTFS
        # alternate streams. Even an HTTP 200 must not certify these paths.
        for source in ("C:/plugins/demo", "plugins/demo:alternate", "C:relative"):
            with self.subTest(source=source):
                marketplace = json.dumps({
                    "plugins": [{"name": "demo", "source": source}]
                }).encode("utf-8")
                encoded = source.replace(":", "%3A")
                with patch(
                    "urllib.request.urlopen",
                    side_effect=serves({
                        ".claude-plugin/marketplace.json": marketplace,
                        f"{encoded}/.claude-plugin/plugin.json": None,
                    }),
                ) as urlopen:
                    self.assertEqual(
                        MODULE.probe_claude_marketplace("example", "mods", "demo"),
                        (True, None),
                    )
                    self.assertEqual(urlopen.call_count, 1)

    def test_rejects_percent_encoded_marketplace_paths(self) -> None:
        # Marketplace source is a directory path, not a URL. Encoded dot/slash
        # segments can be decoded after the initial traversal check.
        for source in ("plugins/%2e%2e/private", "plugins/%2fprivate", "plugins/%252e%252e/private"):
            with self.subTest(source=source):
                marketplace = ('{"plugins":[{"name":"x","source":"%s"}]}' % source).encode()
                with patch(
                    "urllib.request.urlopen",
                    serves({
                        ".claude-plugin/marketplace.json": marketplace,
                        f"{source}/.claude-plugin/plugin.json": None,
                    }),
                ):
                    self.assertEqual(
                        MODULE.probe_claude_marketplace("example", "mods", "x"),
                        (True, None),
                    )

    def test_quotes_literal_spaced_and_unicode_marketplace_directories(self) -> None:
        for source, encoded in (
            ("plugins/my workspace", "plugins/my%20workspace"),
            ("plugins/café", "plugins/caf%C3%A9"),
        ):
            with self.subTest(source=source):
                marketplace = json.dumps({
                    "plugins": [{"name": "demo", "source": source}]
                }).encode("utf-8")
                with patch(
                    "urllib.request.urlopen",
                    side_effect=serves({
                        ".claude-plugin/marketplace.json": marketplace,
                        f"{encoded}/.claude-plugin/plugin.json": None,
                    }),
                ) as urlopen:
                    self.assertEqual(
                        MODULE.probe_claude_marketplace("example", "mods", "demo"),
                        (True, "https://raw.githubusercontent.com/example/mods/HEAD/"
                         f"{encoded}/.claude-plugin/plugin.json"),
                    )
                    self.assertEqual(urlopen.call_count, 2)

    def test_rejects_url_delimiters_in_marketplace_source_paths(self) -> None:
        # A query or fragment must not change which manifest a raw URL probes.
        # Simulate a 200 response for the malformed target: successful HTTP
        # alone must not certify a repository-relative plugin directory.
        for source in ("plugins/demo?branch=main", "plugins/demo#preview"):
            with self.subTest(source=source):
                marketplace = json.dumps({
                    "plugins": [{"name": "demo", "source": source}]
                }).encode("utf-8")
                with patch(
                    "urllib.request.urlopen",
                    side_effect=serves({
                        ".claude-plugin/marketplace.json": marketplace,
                        f"{source}/.claude-plugin/plugin.json": None,
                    }),
                ) as urlopen:
                    self.assertEqual(
                        MODULE.probe_claude_marketplace("example", "mods", "demo"),
                        (True, None),
                    )
                    # Invalid paths are rejected before any manifest HEAD.
                    self.assertEqual(urlopen.call_count, 1)

    def test_rejects_json_escaped_unpaired_surrogates_before_url_encoding(self) -> None:
        # JSON accepts escaped lone surrogates but urllib.parse.quote cannot
        # encode them to UTF-8. One bad upstream entry must not abort the feed.
        for source in ("plugins/" + chr(0xD800), "plugins/" + chr(0xDFFF)):
            with self.subTest(source=repr(source)):
                marketplace = json.dumps({
                    "plugins": [{"name": "demo", "source": source}]
                }).encode("utf-8")
                with patch(
                    "urllib.request.urlopen",
                    side_effect=serves({
                        ".claude-plugin/marketplace.json": marketplace,
                    }),
                ) as urlopen:
                    self.assertEqual(
                        MODULE.probe_claude_marketplace("example", "mods", "demo"),
                        (True, None),
                    )
                    self.assertEqual(urlopen.call_count, 1)

    def test_rejects_control_characters_in_marketplace_sources(self) -> None:
        # Reject invalid URL characters before even attempting the manifest
        # probe; malformed upstream metadata must not abort catalog generation.
        for source in ("plugins/name\nbad", "plugins/name\rhidden",
                       "plugins/name\tbad", "plugins/name\u0000bad"):
            with self.subTest(source=repr(source)):
                marketplace = json.dumps({
                    "plugins": [{"name": "x", "source": source}]
                }).encode("utf-8")
                with patch(
                    "urllib.request.urlopen",
                    side_effect=serves({
                        ".claude-plugin/marketplace.json": marketplace,
                    }),
                ) as urlopen:
                    self.assertEqual(
                        MODULE.probe_claude_marketplace("example", "mods", "x"),
                        (True, None),
                    )
                    self.assertEqual(urlopen.call_count, 1)

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

    def test_truncated_marketplace_http_body_does_not_abort_catalog(self) -> None:
        import http.client

        class TruncatedResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return None

            def read(self, _limit):
                raise http.client.IncompleteRead(b'{"plugins":[', 20)

        with patch("urllib.request.urlopen", return_value=TruncatedResponse()) as fetch:
            self.assertEqual(
                MODULE.probe_claude_marketplace(
                    "example", "mods", "sidequest", require_named_match=True,
                ),
                (False, None),
            )
            self.assertEqual(fetch.call_count, 1)

        # An interrupted marketplace read also cannot assign a generic root
        # Codex manifest to either named Claude bundle from the same repo.
        with tempfile.TemporaryDirectory() as folder:
            readme = Path(folder) / "README.md"
            readme.write_text(
                "## Community Plugins\n### Development & Workflow\n"
                "- [Sidequest](https://github.com/example/mods) - Tasks.\n"
                "- [Quality Gate](https://github.com/example/mods) - Tests.\n"
            )
            with (
                patch("urllib.request.urlopen", return_value=TruncatedResponse()),
                patch.object(MODULE, "probe_install_url") as root_probe,
            ):
                plugins, added = MODULE.merge_readme_additions([], readme)
            self.assertEqual((plugins, added), ([], 0))
            root_probe.assert_not_called()

    def test_bad_root_manifest_head_reply_does_not_abort_catalog(self) -> None:
        import http.client

        with patch(
            "urllib.request.urlopen",
            side_effect=http.client.BadStatusLine("broken root manifest HEAD"),
        ) as fetch:
            self.assertIsNone(
                MODULE.probe_install_url("example", "mods")
            )
            # Each supported candidate fails independently; one bad reply
            # must not prematurely suppress the others or invent a manifest.
            self.assertEqual(fetch.call_count, len(MODULE.INSTALL_PATH_CANDIDATES))

    def test_bad_nested_manifest_head_response_is_a_source_miss(self) -> None:
        import http.client

        marketplace = b'{"plugins":[{"name":"sidequest","source":"plugins/sidequest"}]}'
        read_marketplace = serves({".claude-plugin/marketplace.json": marketplace})

        def malformed_manifest_probe(request, *args, **kwargs):
            if getattr(request, "get_method", lambda: None)() == "HEAD":
                raise http.client.BadStatusLine("broken nested manifest HEAD")
            return read_marketplace(request, *args, **kwargs)

        with patch(
            "urllib.request.urlopen", side_effect=malformed_manifest_probe,
        ) as fetch:
            self.assertEqual(
                MODULE.probe_claude_marketplace(
                    "example", "mods", "sidequest", require_named_match=True,
                ),
                (True, None),
            )
            self.assertEqual(fetch.call_count, 2)

    def test_bad_http_status_line_is_a_source_miss_not_build_failure(self) -> None:
        import http.client

        class InvalidHttpResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return None

            def read(self, _limit):
                raise http.client.BadStatusLine("broken upstream response")

        with patch("urllib.request.urlopen", return_value=InvalidHttpResponse()) as fetch:
            self.assertEqual(
                MODULE.probe_claude_marketplace(
                    "example", "mods", "sidequest", require_named_match=True,
                ),
                (False, None),
            )
            self.assertEqual(fetch.call_count, 1)

    def test_excessively_nested_marketplace_does_not_abort_catalog(self) -> None:
        # A bounded HTTP body can still have enough nesting for json.loads
        # to raise RecursionError. Ignore this source, not the full registry.
        malformed = (
            b'{"plugins":' + b'[' * 12000 + b'0'
            + b']' * 12000 + b'}'
        )
        with patch(
            "urllib.request.urlopen",
            side_effect=serves({".claude-plugin/marketplace.json": malformed}),
        ) as urlopen:
            self.assertEqual(
                MODULE.probe_claude_marketplace("example", "mods", "demo"),
                (True, None),
            )
            self.assertEqual(urlopen.call_count, 1)

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
