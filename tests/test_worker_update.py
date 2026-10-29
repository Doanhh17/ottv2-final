import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

from scripts.update_worker import install_source, read_archive


class WorkerUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.project=self.root/'app';self.project.mkdir()
        self.old={'ottv2/rules.py':b'old policy\n','deploy/ottv2-worker-pool.service':b'old unit\n'}
        for name,data in self.old.items():
            p=self.project/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
        self.manifest=self.hashes(self.old)
        (self.project/'SHA256SUMS').write_bytes(self.manifest)
        self.archive=self.root/'new.zip'
        self.new={**self.old,'ottv2/rules.py':b'new policy\n','docs/README.md':b'new guide\n'}
        self.write_zip(self.new)

    def hashes(self,files):
        return ''.join(hashlib.sha256(data).hexdigest()+'  '+name+'\n' for name,data in sorted(files.items())).encode()

    def write_zip(self,files,extra=None):
        with zipfile.ZipFile(self.archive,'w') as z:
            for name,data in files.items():z.writestr('ottv2/'+name,data)
            z.writestr('ottv2/SHA256SUMS',self.hashes(files))
            if extra:z.writestr(*extra)

    def test_update_preserves_private_keys_outbox_and_unlisted_local_data(self):
        private=self.project/'output/private-workers/key.json'
        private.parent.mkdir(parents=True);private.write_bytes(b'private credential')
        outbox=self.project/'output/outbox.json';outbox.write_bytes(b'unacknowledged result')
        result=install_source(self.archive,self.project,self.root/'backup')
        self.assertGreater(result['changed'],0)
        for name,data in self.new.items():self.assertEqual((self.project/name).read_bytes(),data)
        self.assertEqual(private.read_bytes(),b'private credential')
        self.assertEqual(outbox.read_bytes(),b'unacknowledged result')
        self.assertEqual((self.root/'backup/ottv2/rules.py').read_bytes(),self.old['ottv2/rules.py'])

    def test_independent_source_edit_is_not_overwritten(self):
        p=self.project/'ottv2/rules.py';p.write_bytes(b'local edits')
        with self.assertRaises(ValueError):install_source(self.archive,self.project,self.root/'backup')
        self.assertEqual(p.read_bytes(),b'local edits')
        self.assertFalse((self.root/'backup').exists())

    def test_unlisted_traversal_and_tampered_archive_are_rejected(self):
        self.write_zip(self.new,('ottv2/../../key.json',b'bad'))
        with self.assertRaises(ValueError):read_archive(self.archive)
        with zipfile.ZipFile(self.archive,'w') as z:
            for name,data in self.new.items():z.writestr('ottv2/'+name,b'tampered' if name=='ottv2/rules.py' else data)
            z.writestr('ottv2/SHA256SUMS',self.hashes(self.new))
        with self.assertRaises(ValueError):install_source(self.archive,self.project,self.root/'backup')
        self.assertEqual((self.project/'SHA256SUMS').read_bytes(),self.manifest)

    def test_failed_write_rolls_back_existing_sources_and_manifest(self):
        from scripts.update_worker import atomic_write
        failed=[False]
        def write(path,*args,**kwargs):
            if path.name=='rules.py' and not failed[0]:
                failed[0]=True;raise OSError('simulated storage failure')
            return atomic_write(path,*args,**kwargs)
        with patch('scripts.update_worker.atomic_write',side_effect=write):
            with self.assertRaises(OSError):install_source(self.archive,self.project,self.root/'backup')
        for name,data in self.old.items():self.assertEqual((self.project/name).read_bytes(),data)
        self.assertEqual((self.project/'SHA256SUMS').read_bytes(),self.manifest)
        self.assertFalse((self.project/'docs/README.md').exists())


if __name__=='__main__':unittest.main()
