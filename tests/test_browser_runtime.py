import asyncio
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import unittest


class BrowserRuntimeTests(unittest.TestCase):
    def runtime(self):
        path=Path(__file__).resolve().parent.parent/'practice/runtime.py'
        spec=importlib.util.spec_from_file_location('browser_runtime_test',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module

    def exercise(self,source,inputs):
        module=self.runtime();output=io.StringIO()
        async def run():
            task=asyncio.create_task(module.run_bot(source))
            await asyncio.sleep(0)
            for line in inputs:
                module._ott_feed(line+'\n')
                await asyncio.sleep(0)
            module._ott_feed('')
            await asyncio.wait_for(task,2)
        with redirect_stdout(output):asyncio.run(run())
        return output.getvalue().splitlines()

    def test_stdin_loop_keeps_python_state_between_turns(self):
        source='import sys\nprint("READY",flush=True)\nn=0\nfor line in sys.stdin:\n n+=1\n print(n,line.strip(),flush=True)'
        self.assertEqual(self.exercise(source,['first','second']),['READY','1 first','2 second'])

    def test_readline_through_helper_and_main_keeps_state(self):
        source='import sys\ndef read():\n return sys.stdin.readline()\ndef main():\n print("READY")\n n=0\n while True:\n  line=read()\n  if not line:break\n  n+=1\n  print(n,line.strip())\nif __name__=="__main__":main()'
        self.assertEqual(self.exercise(source,['a','b']),['READY','1 a','2 b'])

    def test_builtin_input_and_import_alias(self):
        source='from sys import stdin as stream\nfor line in stream:\n print(line.strip())'
        self.assertEqual(self.exercise(source,['a','b']),['a','b'])
        self.assertEqual(self.exercise('def main():\n print(input())\n print(input())\nmain()',['x','y']),['x','y'])

    def test_shipped_bot_accepts_two_separate_state_messages(self):
        root=Path(__file__).resolve().parent.parent
        state=json.loads((root/'maps/nine-piece.json').read_text())
        message=json.dumps({**state,'you':'A','turn':'A','type':'state','protocol_version':1})
        lines=self.exercise((root/'examples/greedy/main.py').read_text(),[message,message])
        self.assertEqual(len(lines),3)
        self.assertEqual(json.loads(lines[0])['type'],'ready')
        self.assertEqual(set(json.loads(lines[1])),{'from','to'})
