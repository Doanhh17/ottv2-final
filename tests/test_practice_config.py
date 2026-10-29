import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ottv2 import match_queue, queue_admin, practice_config, portal_data


class PracticeConfigTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.store=self.root/'bots';self.store.mkdir()
        self.db=self.root/'queue.sqlite3';self.map=Path(__file__).resolve().parent.parent/'maps/nine-piece.json'
        match_queue.initialize(self.db,self.store,self.map)

    def test_export_has_current_map_three_python_bots_and_no_private_configuration(self):
        cfg=portal_data.config(self.db);cfg['secret']='never exported'
        exported=practice_config.export(cfg)
        self.assertEqual(set(exported),{'schema_version','type','map','bots','limits','config_sha256'})
        self.assertEqual(exported['limits']['max_valid_moves'],50)
        self.assertEqual(exported['limits']['cpu_budget_seconds'],7)
        self.assertEqual(exported['map']['board'],cfg['initial_state']['board'])
        self.assertEqual(set(exported['bots']),set(practice_config.LEVELS))
        self.assertNotIn('never exported',json.dumps(exported))
        self.assertNotEqual(exported['bots']['intermediate'],exported['bots']['advanced'])

    def test_three_bots_and_map_commit_together_and_invalid_code_rolls_back(self):
        queue_admin.set_paused(self.db,True)
        bots=dict.fromkeys(practice_config.LEVELS,'print("one")\n')
        queue_admin.change_map(self.db,self.map,bots)
        original=portal_data.config(self.db)
        bad={**bots,'advanced':'if :'}
        other=self.map.parent/'pool-examples/open.json'
        with self.assertRaisesRegex(ValueError,'cú pháp'):
            queue_admin.change_map(self.db,other,bad)
        self.assertEqual(portal_data.config(self.db),original)
        queue_admin.change_map(self.db,other,{**bots,'advanced':'print("two")'})
        cfg=portal_data.config(self.db)
        self.assertEqual(cfg['map']['name'],'open')
        self.assertEqual(practice_config.export(cfg)['bots']['advanced'],'print("two")')

    def test_pause_error_precedes_parsing_and_never_executes_operator_source(self):
        with self.assertRaisesRegex(ValueError,'pause'):
            queue_admin.change_map(self.db,self.root/'missing.json',{})
        queue_admin.set_paused(self.db,True)
        marker=self.root/'must-not-exist'
        source=f'from pathlib import Path\nPath({str(marker)!r}).touch()\n'
        queue_admin.change_map(self.db,self.map,dict.fromkeys(practice_config.LEVELS,source))
        self.assertFalse(marker.exists())
        self.assertFalse(match_queue.initialize(self.db,self.store,self.map)['initialized'])

    def test_missing_and_oversized_bots_are_rejected(self):
        for bots in ({},dict.fromkeys(practice_config.LEVELS,'#'+'x'*65536),dict.fromkeys(practice_config.LEVELS,'')):
            with self.assertRaises(ValueError):practice_config.validate_bots(bots)


if __name__=='__main__':unittest.main()
