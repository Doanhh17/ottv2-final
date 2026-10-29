import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from ottv2.cpu_trial import run_trial
from ottv2.map_config import load_map
from ottv2.map_pool import choose_map, write_examples
from ottv2.protocol import state_message


class MapPoolTests(unittest.TestCase):
    def test_all_candidates_can_be_selected_and_preserve_full_state(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp) / "pool"
            write_examples(directory)
            for number, path in enumerate(sorted(directory.glob("*.json"))):
                with patch("ottv2.map_pool.secrets.choice", side_effect=lambda candidates: candidates[number]):
                    game, meta = choose_map(directory, "B")
                expected, info = load_map(path, "B")
                self.assertEqual(state_message(game), state_message(expected))
                self.assertEqual(meta, {**info, "selection": "pool_random"})
            with self.assertRaises(FileExistsError):
                write_examples(directory)

    def test_invalid_unchosen_map_rejects_entire_pool_before_drawing(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp) / "pool"
            write_examples(directory)
            (directory / "invalid.json").write_text('{}')
            with patch("ottv2.map_pool.secrets.choice") as draw, self.assertRaises(ValueError):
                choose_map(directory)
            draw.assert_not_called()

    def test_duplicate_layout_is_rejected_even_with_different_name(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp) / "pool"
            write_examples(directory)
            obj = json.loads((directory / "open.json").read_text())
            obj["name"] = "duplicate"
            (directory / "duplicate.json").write_text(json.dumps(obj))
            with self.assertRaisesRegex(ValueError, "duplicate"):
                choose_map(directory)

    def test_empty_missing_and_oversized_pools_fail(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for path in (directory, directory / "missing"):
                with self.assertRaises(ValueError):
                    choose_map(path)
            for number in range(33):
                (directory / f"{number}.json").write_text('{}')
            with self.assertRaisesRegex(ValueError, "32"):
                choose_map(directory)

    def test_conflicting_options_and_invalid_pool_start_no_bots(self):
        with TemporaryDirectory() as tmp:
            factory = Mock()
            with patch("ottv2.cpu_trial.sys.platform", "linux"):
                for kwargs in ({"map_pool": tmp}, {"map_pool": tmp, "map_file": "missing.json"}):
                    with self.assertRaises(ValueError):
                        run_trial(session_factory=factory, **kwargs)
            factory.assert_not_called()

    @unittest.skipUnless(sys.platform.startswith("linux") and Path("/usr/local/bin/isolate").is_file(),
                         "requires configured Isolate")
    def test_pool_match_records_only_chosen_map_and_full_board(self):
        with TemporaryDirectory() as tmp:
            directory, output = Path(tmp) / "pool", Path(tmp) / "match.json"
            write_examples(directory)
            # Force the open map for reproducible integration coverage of a changed map.
            with patch("ottv2.map_pool.secrets.choice", side_effect=lambda candidates: candidates[-1]):
                run_trial(map_pool=directory, result_file=output, emit=lambda _: None)
            record = json.loads(output.read_text())
            game, info = load_map(directory / "open.json", record["initial_state"]["turn"])
            self.assertEqual(record["map"], {**info, "selection": "pool_random"})
            self.assertEqual(record["initial_state"], state_message(game))
            for event in record["history"]:
                self.assertEqual(game.play({"from": event["from"], "to": event["to"]}), event)
            self.assertEqual(record["final_state"], state_message(game))
            self.assertTrue(record["cleanup_complete"])


if __name__ == "__main__":
    unittest.main()
