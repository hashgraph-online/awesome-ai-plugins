from __future__ import annotations

import importlib.util
import tempfile
import unittest
from datetime import date
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts/generate_plugins_json.py"
SPEC = importlib.util.spec_from_file_location("generate_plugins_json", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class GeneratePluginsJsonTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
