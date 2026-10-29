import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from ottv2.cpu_trial import run_trial
from ottv2.engine import sample_game
from ottv2.map_config import load_map, write_example
from ottv2.protocol import state_message


class MapConfigTests(unittest.TestCase):
    def test_example_preserves_map_and_selects_first_side(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "map.json"
            write_example(path)
            for first in ("A", "B"):
                game, meta = load_map(path, first)
                self.assertEqual(state_message(game), state_message(sample_game(first)))
                self.assertEqual(meta["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            with self.assertRaises(FileExistsError):
                write_example(path)

    def test_rejects_wrong_counts_types_overlaps_and_coordinates(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "map.json"
            write_example(path)
            original = json.loads(path.read_text())
            def change_piece(obj):
                obj["board"]["a1"]["kind"] = "paper"
            def change_coordinate(obj):
                obj["board"]["j1"] = obj["board"].pop("a1")
            changes = [change_piece, change_coordinate,
                       lambda o: o["bombs"].append("a1"),
                       lambda o: o["obstacles"].append("i9"),
                       lambda o: o["bombs"].append("d5"),
                       lambda o: o.update(schema_version=True),
                       lambda o: o.update(name=""),
                       lambda o: o.update(board=[]),
                       lambda o: o.update(goals={"A": [], "B": "a1"}),
                       lambda o: o.update(bombs=[{}]),
                       lambda o: o["goals"].update(A="a1"),
                       lambda o: o.update(extra=True)]
            for change in changes:
                with self.subTest(change=change):
                    obj = json.loads(json.dumps(original))
                    change(obj)
                    path.write_text(json.dumps(obj))
                    with self.assertRaises(ValueError):
                        load_map(path)

    def test_rejects_duplicate_keys_bad_encoding_and_large_input(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            for raw in (b'{"board":{},"board":{}}', b'\xff', b'{', b' ' * 65537):
                path.write_bytes(raw)
                with self.assertRaises(ValueError):
                    load_map(path)

    def test_invalid_map_fails_before_starting_bots_or_saving_result(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text('{}')
            factory = Mock()
            target = Path(tmp) / "result.json"
            with patch("ottv2.cpu_trial.sys.platform", "linux"), self.assertRaises(ValueError):
                run_trial(map_file=path, session_factory=factory, result_file=target)
            factory.assert_not_called()
            self.assertFalse(target.exists())

    @unittest.skipUnless(sys.platform.startswith("linux") and Path("/usr/local/bin/isolate").is_file(),
                         "requires configured Isolate")
    def test_configured_map_runs_in_sandbox_and_records_provenance(self):
        with TemporaryDirectory() as tmp:
            path, result = Path(tmp) / "map.json", Path(tmp) / "result.json"
            write_example(path)
            game, _, _ = run_trial(map_file=path, result_file=result, emit=lambda _: None)
            record = json.loads(result.read_text())
            self.assertIsNotNone(game.result)
            self.assertEqual(len(record["initial_state"]["board"]), 18)
            self.assertEqual(record["map"]["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(record["limits"]["memory_limit_mib_per_bot"], 64)
            self.assertTrue(record["cleanup_complete"])


if __name__ == "__main__":
    unittest.main()
