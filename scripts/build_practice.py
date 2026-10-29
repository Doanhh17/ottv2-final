"""Build the downloadable practice page with embedded public configuration."""
import base64
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from ottv2.practice_config import export
from ottv2.map_config import load_map
from ottv2.protocol import state_message


def build():
    game,meta=load_map(ROOT/'maps/nine-piece.json')
    config=export({'initial_state':state_message(game),'map':meta})
    def js(value):
        return json.dumps(value,ensure_ascii=False).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    text=(ROOT/'practice/template.html').read_text(encoding='utf-8')
    for key,value in {'__LOGO__':base64.b64encode((ROOT/'web/assets/uet-logo.png').read_bytes()).decode(),
                      '__CORE__':(ROOT/'practice/core.js').read_text(encoding='utf-8'),
                      '__CONFIG__':js(config),'__RUNTIME__':js((ROOT/'practice/runtime.py').read_text(encoding='utf-8'))}.items():
        text=text.replace(key,value)
    (ROOT/'practice/index.html').write_text(text,encoding='utf-8',newline='\n')


if __name__=='__main__':
    build()
    print('Đã tạo practice/index.html')
