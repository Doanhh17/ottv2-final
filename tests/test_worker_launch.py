from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from scripts import start_worker
from scripts.configure_worker import validate_replacement


class LauncherTests(unittest.TestCase):
    def test_local_desktop_host_has_six_slots(self):
        self.assertEqual(start_worker.HOSTS['worker_local'],('worker-local-pool',6))

    def test_local_button_selects_its_canonical_vps_key(self):
        with TemporaryDirectory() as tmp:
            folder=Path(tmp)/'output/private-workers';folder.mkdir(parents=True)
            selected=folder/'worker-local-pool-vps.json';selected.write_text('{}')
            (folder/'worker-local-pool.json').write_text('{}')
            with patch.object(start_worker.worker_pool,'load_config',return_value={'host_id':'worker-local-pool'}):
                self.assertEqual(start_worker.find_config(tmp,'worker-local-pool'),selected)

    def test_desktop_does_not_start_another_manual_pool(self):
        def run(args,**kwargs):
            return Mock(returncode=1 if args[0]=='systemctl' else 0)
        with patch('sys.platform','linux'),patch('sys.argv',['start_worker.py','--name','worker_local']), \
                patch.object(start_worker.os,'getuid',return_value=1000,create=True), \
                patch.object(start_worker.subprocess,'run',side_effect=run) as called:
            start_worker.main()
        actions=[call.args[0] for call in called.call_args_list]
        self.assertEqual(len(actions),2)
        self.assertEqual(actions[1][0],'pgrep')

    def test_desktop_preserves_existing_manual_profile_and_uses_same_key(self):
        config=Path('/test/worker.json')
        def run(args,**kwargs):
            return Mock(returncode=1 if args[0] in ('systemctl','pgrep') else 0)
        with patch('sys.platform','linux'),patch('sys.argv',['start_worker.py','--name','worker_local']), \
                patch.object(start_worker.os,'getuid',return_value=1000,create=True), \
                patch.object(start_worker,'find_config',return_value=config), \
                patch.object(start_worker.worker_pool,'load_profile',return_value={'max_slots':6}), \
                patch.object(start_worker.worker_pool,'configure_profile') as configure, \
                patch.object(start_worker.subprocess,'run',side_effect=run) as called:
            start_worker.main()
        configure.assert_not_called()
        runs=[call.args[0] for call in called.call_args_list if '-m' in call.args[0]]
        self.assertEqual(len(runs),1)
        self.assertIn(str(config),runs[0])
        self.assertIn('shared',runs[0])

    def test_extended_config_cannot_change_host_url_or_an_old_key(self):
        old={'host_id':'worker-pc02','url':'https://example.org','workers':[{'worker_id':'worker-pc02-01','token':'old'}]}
        new={**old,'workers':old['workers']+[{'worker_id':'worker-pc02-02','token':'new'}]}
        validate_replacement(old,new)
        for bad in ({**new,'host_id':'worker-pc04'},{**new,'url':'https://other.example'},
                    {**new,'workers':[{'worker_id':'worker-pc02-01','token':'changed'}]}):
            with self.assertRaises(ValueError):validate_replacement(old,bad)
    def test_existing_service_profile_is_not_reset_or_restarted_each_day(self):
        def run(args,**kwargs):
            return Mock(returncode=0,stdout='unchanged\n')
        with patch('sys.platform','linux'), patch('sys.argv',['start_worker.py','--name','worker_sinh']), patch.object(start_worker.subprocess,'run',side_effect=run) as called:
            start_worker.main()
        actions=[call.args[0] for call in called.call_args_list]
        self.assertIn(['sudo','systemctl','start','ottv2-worker-pool.service'],actions)
        self.assertNotIn(['sudo','systemctl','restart','ottv2-worker-pool.service'],actions)
        configure=next(a for a in actions if '-c' in a)
        self.assertIn('worker-pc02',configure[-1])
        self.assertIn('old is None',configure[-1])

    def test_config_lookup_selects_host_identity_instead_of_filename(self):
        with TemporaryDirectory() as tmp:
            folder=Path(tmp)/'output/private-workers';folder.mkdir(parents=True)
            selected=folder/'unrelated-filename.json'; selected.write_text('{}')
            other=folder/'worker-pc02.json';other.write_text('{}')
            def load(path):
                if path==selected:return {'host_id':'worker-pc02'}
                return {'host_id':'worker-pc04'}
            with patch.object(start_worker.worker_pool,'load_config',side_effect=load):
                self.assertEqual(start_worker.find_config(tmp,'worker-pc02'),selected)
                with self.assertRaises(ValueError):start_worker.find_config(tmp,'missing')

