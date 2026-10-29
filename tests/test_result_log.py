import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ottv2.result_log import write_result


class ResultLogTests(unittest.TestCase):
    def test_complete_utf8_record_round_trips_without_temporary_files(self):
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "matches" / "one.json"
            record = {"schema_version": 1, "detail": "Búa thắng kéo", "winner": "A"}
            self.assertEqual(write_result(target, record), target)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), record)
            self.assertEqual(list(target.parent.iterdir()), [target])

    def test_existing_file_is_preserved(self):
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "one.json"
            target.write_text("previous result", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                write_result(target, {"winner": "B"})
            self.assertEqual(target.read_text(), "previous result")

    def test_bad_serialization_does_not_create_a_result(self):
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "one.json"
            with self.assertRaises(ValueError):
                write_result(target, {"cpu": float("nan")})
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_publish_error_removes_temporary_file_and_does_not_claim_success(self):
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "one.json"
            with patch("ottv2.result_log.os.link", side_effect=OSError("test disk failure")):
                with self.assertRaisesRegex(OSError, "disk failure"):
                    write_result(target, {"winner": "A"})
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_competing_result_created_during_publish_is_not_overwritten(self):
        link = os.link
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "one.json"

            def compete(source, destination):
                target.write_text("other match result", encoding="utf-8")
                return link(source, destination)

            with patch("ottv2.result_log.os.link", side_effect=compete):
                with self.assertRaises(FileExistsError):
                    write_result(target, {"winner": "A"})
            self.assertEqual(target.read_text(), "other match result")
            self.assertEqual(list(Path(temporary).iterdir()), [target])
