import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

from scripts.build_release import build
from scripts.render_deploy import render


class DeploymentTests(unittest.TestCase):
    def test_rendered_master_worker_and_proxy_share_the_public_origin(self):
        with TemporaryDirectory() as temporary:
            destination=render('Arena.Example.com',Path(temporary)/'config',distributed=True)
            files={p.name:p.read_text(encoding='utf-8') for p in destination.iterdir()}
        self.assertIn('OTTV2_PUBLIC_ORIGIN=https://arena.example.com',files['ottv2.env'])
        self.assertIn('--port 8765 --distributed --public-origin ${OTTV2_PUBLIC_ORIGIN}',files['ottv2.service'])
        self.assertIn('--config /etc/ottv2-worker.json',files['ottv2-worker.service'])
        self.assertIn('--config /etc/ottv2-worker-pool.json',files['ottv2-worker-pool.service'])
        self.assertIn('TimeoutStopSec=330',files['ottv2-worker-pool.service'])
        self.assertIn('TimeoutStopSec=330',files['ottv2-worker.service'])
        self.assertIn('TimeoutStopSec=360',files['ottv2.service'])
        self.assertIn('server_name arena.example.com;',files['ottv2.conf'])
        self.assertIn('location = /api/worker',files['ottv2.conf'])
        self.assertIn('client_max_body_size 16m;',files['ottv2.conf'])
        self.assertIn('zone=ottv2_worker_ip:1m rate=40r/s;',files['ottv2.conf'])
        worker_location=files['ottv2.conf'].split('location = /api/worker {',1)[1].split('}',1)[0]
        self.assertIn('limit_req zone=ottv2_worker_ip burst=80 nodelay;',worker_location)
        self.assertIn('limit_req zone=ottv2_login burst=10;',files['ottv2.conf'])
        self.assertIn('proxy_pass http://127.0.0.1:8765;',files['ottv2-proxy.conf'])
        self.assertIn('proxy_set_header Host arena.example.com;',files['ottv2-proxy.conf'])
        self.assertFalse(any('@@DOMAIN@@' in value for value in files.values()))

    def test_invalid_domains_create_no_deployment_directory(self):
        with TemporaryDirectory() as temporary:
            for domain in ('https://example.com','example.com/path','127.0.0.1','example.com;bad',
                           '-bad.example','example..com','localhost','example.com:443'):
                with self.subTest(domain=domain):
                    destination=Path(temporary)/'config'
                    with self.assertRaises(ValueError):render(domain,destination)
                    self.assertFalse(destination.exists())

    def test_source_archive_is_complete_verifiable_and_excludes_live_data(self):
        with TemporaryDirectory() as temporary:
            destination=Path(temporary)/'source.zip'
            count=build(destination)
            with zipfile.ZipFile(destination) as archive:
                names=archive.namelist()
                manifest=archive.read('ottv2/SHA256SUMS').decode('utf-8').splitlines()
                self.assertEqual(count,len(names)-1)
                self.assertEqual(count,len(manifest))
                for line in manifest:
                    digest,name=line.split('  ',1)
                    self.assertEqual(hashlib.sha256(archive.read('ottv2/'+name)).hexdigest(),digest)
                for name in names:
                    self.assertTrue(name.startswith('ottv2/'))
                    self.assertFalse(set(Path(name).parts)&{'output','.git','__pycache__','private-workers'})
                    self.assertFalse(name.endswith(('.sqlite3','google-client.json','outbox.json','.tar.gz')))
                for required in ('README.md','docs/README.md','docs/MOI_TRUONG.md','docs/VAN_HANH.md','docs/QUY_DINH_SINH_VIEN.md',
                                 'docs/TRIEN_KHAI_VPS.md','docs/MAY_CHAM.md','ottv2/cluster.py','ottv2/remote_worker.py',
                                 'deploy/ottv2-worker.service','deploy/ottv2-worker-pool.service',
                                 'ottv2/worker_pool.py','ottv2/rules.py','scripts/update_worker.py',
                                 'scripts/load_test_server.py','scripts/measure_students.cjs',
                                 'docs/BAN_DO.md','docs/TOI_UU.md','scripts/start-worker.cmd','scripts/install_worker_desktop.ps1',
                                 'deploy/ottv2-vps-worker.service','web/assets/uet-logo.png','google/appsscript.json','replay/index.html',
                                 'practice/index.html','practice/core.js','practice/runtime.py','docs/DAU_THU.md'):
                    self.assertIn('ottv2/'+required,names)
                self.assertNotIn('ottv2/docs/SPEC.md',names)
                self.assertNotIn('ottv2/docs/SU_DUNG.md',names)
            with self.assertRaises(FileExistsError):build(destination)

    def test_incomplete_source_tree_is_not_published(self):
        with TemporaryDirectory() as temporary:
            root=Path(temporary);destination=root/'source.zip'
            with patch('scripts.build_release.__file__',str(root/'scripts/build_release.py')):
                with self.assertRaisesRegex(ValueError,'Missing required release source'):build(destination)
            self.assertFalse(destination.exists())


if __name__=='__main__':unittest.main()
