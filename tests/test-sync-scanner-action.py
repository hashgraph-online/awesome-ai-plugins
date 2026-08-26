from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts/sync-scanner-action.py"
SPEC = importlib.util.spec_from_file_location("sync_scanner_action", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SyncScannerActionTests(unittest.TestCase):
    def test_resolve_commit_follows_annotated_tag(self) -> None:
        commit = "b" * 40
        with patch.object(
            MODULE,
            "fetch_json",
            return_value={"object": {"type": "commit", "sha": commit}},
        ):
            self.assertEqual(
                MODULE.resolve_commit({"type": "tag", "sha": "a" * 40}),
                commit,
            )

    def test_update_files_replaces_all_consistent_pins(self) -> None:
        old_commit = "a" * 40
        new_commit = "b" * 40
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / f"file-{index}.yml" for index in range(2)]
            for path in paths:
                path.write_text(
                    f"uses: hashgraph-online/ai-plugin-scanner-action@{old_commit} # v1.0.0\n"
                )
            with patch.object(MODULE, "PINNED_FILES", tuple(paths)):
                changed = MODULE.update_files("v1.1.0", new_commit, check=False)

            self.assertEqual(changed, paths)
            for path in paths:
                self.assertIn(f"@{new_commit} # v1.1.0", path.read_text())

    def test_update_files_is_noop_when_current(self) -> None:
        commit = "b" * 40
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workflow.yml"
            path.write_text(
                f"uses: hashgraph-online/ai-plugin-scanner-action@{commit} # v1.1.0\n"
            )
            with patch.object(MODULE, "PINNED_FILES", (path,)):
                self.assertEqual(MODULE.update_files("v1.1.0", commit, check=False), [])

    def test_update_files_rejects_inconsistent_existing_pins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / f"file-{index}.yml" for index in range(2)]
            paths[0].write_text(
                f"uses: hashgraph-online/ai-plugin-scanner-action@{'a' * 40}\n"
            )
            paths[1].write_text(
                f"uses: hashgraph-online/ai-plugin-scanner-action@{'b' * 40}\n"
            )
            with patch.object(MODULE, "PINNED_FILES", tuple(paths)):
                with self.assertRaisesRegex(RuntimeError, "inconsistent"):
                    MODULE.update_files("v1.1.0", "c" * 40, check=False)


if __name__ == "__main__":
    unittest.main()
