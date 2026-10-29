import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import export_github

class GithubExportTests(unittest.TestCase):
    def test_export_contains_only_approved_sources_and_checksums_not_history_or_runtime(self):
        with TemporaryDirectory() as temporary:
            root=Path(temporary)/'source';root.mkdir()
            (root/'README.md').write_text('current source')
            (root/'.git').mkdir();(root/'.git/private-history').write_text('private discussion')
            (root/'output').mkdir();(root/'output/key.pem').write_text('private runtime')
            destination=Path(temporary)/'public'
            with patch.object(export_github,'ROOT',root),patch.object(export_github,'source_files',return_value=[root/'README.md']):
                result=export_github.export(destination)
            self.assertEqual(result['source_files'],1)
            self.assertEqual({p.name for p in destination.iterdir()},{'README.md','SHA256SUMS'})
            self.assertEqual((destination/'SHA256SUMS').read_text(),hashlib.sha256((root/'README.md').read_bytes()).hexdigest()+'  README.md\n')

    def test_existing_work_is_not_overwritten(self):
        with TemporaryDirectory() as temporary:
            keep=Path(temporary)/'keep';keep.write_text('preserve')
            with self.assertRaises(ValueError):export_github.export(temporary)
            self.assertEqual(keep.read_text(),'preserve')

    def test_possible_private_key_is_rejected_before_creating_export(self):
        with TemporaryDirectory() as temporary:
            source=Path(temporary)/'bad.txt';source.write_text('-----BEGIN PRIVATE KEY-----\nprivate\n')
            destination=Path(temporary)/'public'
            with patch.object(export_github,'ROOT',Path(temporary)),patch.object(export_github,'source_files',return_value=[source]):
                with self.assertRaisesRegex(ValueError,'Possible credential'):export_github.export(destination)
            self.assertFalse(destination.exists())

if __name__=='__main__':unittest.main()
