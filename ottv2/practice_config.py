"""Public map and operator-written Python opponents for browser-only practice."""
import ast
import hashlib
import json
from pathlib import Path

from .rules import CURRENT_LIMITS

LEVELS = ('beginner','intermediate','advanced')
MAX_BOT_BYTES = 65536


def default_bots():
    root=Path(__file__).resolve().parent.parent/'examples'
    def search_bot(style,depth):
        source=(root/f'depth3-{style}/main.py').read_text(encoding='utf-8')
        # Practice uses a fixed search depth; omit the unused adaptive entry point.
        source=source.split('\ndef advanced_move(',1)[0]+source[source.index('\nimport json,sys\n'):]
        assert source.count('search.negamax(3,')==1
        source=source.replace('search.negamax(3,',f'search.negamax({depth},',1)
        return '\n'.join(line for line in source.splitlines() if "print('DEPTH_STATS:" not in line)+'\n'
    intermediate=search_bot('alpha',2)
    return {'beginner':(root/'greedy/main.py').read_text(encoding='utf-8'),
            'intermediate':intermediate,'advanced':search_bot('guardian',3)}


def validate_bots(bots):
    if not isinstance(bots,dict) or set(bots)!=set(LEVELS):
        raise ValueError('Cần đủ mã bot sơ cấp, trung cấp và cao cấp.')
    labels=dict(zip(LEVELS,('sơ cấp','trung cấp','cao cấp')))
    for level,source in bots.items():
        if not isinstance(source,str) or not source.strip() or len(source.encode('utf-8'))>MAX_BOT_BYTES:
            raise ValueError(f'Mã bot {labels[level]} cần là main.py, không trống, tối đa 64 KiB.')
        try:ast.parse(source,filename='main.py')
        except (SyntaxError,ValueError,RecursionError):
            raise ValueError(f'Mã bot {labels[level]} có lỗi cú pháp Python. Kiểm tra lại trước khi lưu.') from None
    return dict(bots)


def export(config):
    state=config['initial_state']
    body={'schema_version':1,'type':'ottv2_practice_config',
          'map':{'schema_version':1,'name':config['map']['name'],
                 **{key:state[key] for key in ('board','goals','bombs','obstacles')}},
          'bots':validate_bots(config.get('practice_bots') or default_bots()),'limits':dict(CURRENT_LIMITS)}
    body['config_sha256']=hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    return body
