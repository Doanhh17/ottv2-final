const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process');
const {Game,validateConfig}=require('../practice/core.js');
const root=path.resolve(__dirname,'..');
test('practice rules agree with the official Python engine over 150 deterministic games',()=>{
  const source=`import json,random
from ottv2.engine import sample_game,Game
from ottv2.protocol import state_message
rows=[]
for seed in range(150):
 r=random.Random(seed);g=sample_game(first=r.choice(('A','B')))
 if seed%2:
  positions=r.sample([x+y for x in 'abcdefghi' for y in '123456789' if x+y not in ('i9','a1')],22)
  g=Game(dict(zip(positions[:18],g.board.values())),g.goals,set(positions[18:20]),set(positions[20:]),g.turn)
 g.max_valid_moves=50;g.move_limit_tiebreak='piece_count'
 row={'initial':state_message(g),'moves':[]}
 while g.result is None:
  moves=g.legal_moves()
  if not moves:break
  captures=[m for m in moves if m['to'] in g.board]
  move=r.choice(captures if captures and seed%2 else moves);event=g.play(move)
  row['moves'].append({'event':event,'state':state_message(g)})
 rows.append(row)
print(json.dumps(rows))`;
  const out=cp.execFileSync(process.env.PYTHON||'python',['-X','utf8','-c',source],{cwd:root,maxBuffer:32*1024*1024});
  let captures=0,bombs=0,steps=0;
  for(const row of JSON.parse(out)){const g=new Game(row.initial,row.initial.turn);for(const step of row.moves){const actual=g.play({from:step.event.from,to:step.event.to});assert.deepEqual(actual,step.event);const {time,...state}=g.state({A:7,B:7});assert.deepEqual(state,step.state);captures+=!!actual.capture;bombs+=actual.bomb;steps++;}}
  assert(steps>4000);assert(captures>10);assert(bombs>10);
});
test('practice configuration rejects missing bots, overlapping map and incompatible rules',()=>{
  const html=fs.readFileSync(path.join(root,'practice/index.html'),'utf8');
  const config=JSON.parse(html.match(/const defaultConfig=(.*);/)[1]);
  assert.equal(validateConfig(config).limits.max_valid_moves,50);
  for(const mutate of [c=>delete c.bots.advanced,c=>c.map.obstacles.push('a1'),c=>c.limits.cpu_budget_seconds=10,c=>c.bots.beginner='']){
    const c=structuredClone(config);mutate(c);assert.throws(()=>validateConfig(c));
  }
});
