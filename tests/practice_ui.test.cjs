const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../practice/index.html'),'utf8'),code=html.match(/<script>([\s\S]*)<\/script>/)[1];
class Element{constructor(){this.value='';this.files=[];this.children=[];this.textContent='';this.disabled=false;}append(...v){this.children.push(...v);}replaceChildren(){this.children=[];}scrollIntoView(){}}
function app(){
  const nodes={},workers=[],timers=new Map(),intervals=new Map(),clock={ms:0};let id=0;
  const document={getElementById:id=>nodes[id]??=new Element(),createElement:()=>new Element()};
  document.getElementById('level').value='beginner';document.getElementById('first').value='A';
  document.getElementById('bot-file').files=[{size:10,text:async()=>'# trusted fixture'}];
  document.getElementById('config-input');
  class Worker{constructor(){this.sent=[];workers.push(this);}postMessage(m){this.sent.push(m);}terminate(){this.terminated=true;}emit(data){this.onmessage({data});}}
  const ctx={document,window:{addEventListener(){}},TextEncoder,performance:{now:()=>clock.ms},Worker,Blob:class{},URL:{createObjectURL:()=>String(++id),revokeObjectURL(){}},
    setTimeout:(fn,delay)=>{timers.set(++id,{fn,delay});return id;},clearTimeout:i=>timers.delete(i),setInterval:fn=>{intervals.set(++id,fn);return id;},clearInterval:i=>intervals.delete(i)};
  vm.createContext(ctx);vm.runInContext(code,ctx);
  return {nodes,workers,timers,clock,read:expression=>vm.runInContext(expression,ctx),ready(){for(const w of workers){w.emit({type:'loaded'});w.emit({type:'line',line:'{"protocol_version":1,"type":"ready"}'});}}};
}
test('malformed READY is rejected and both computation workers are stopped',async()=>{
  const a=app();await a.nodes.start.onclick();a.workers[0].emit({type:'loaded'});
  assert.doesNotThrow(()=>a.workers[0].emit({type:'line',line:'null'}));
  assert.match(a.nodes.status.textContent,/READY/);assert(a.workers.every(w=>w.terminated));assert.equal(a.timers.size,0);
});
test('an illegal retry cannot reset the original response deadline',async()=>{
  const a=app();await a.nodes.start.onclick();a.ready();const deadline=a.read('turnDeadline');
  a.clock.ms=2000;a.workers[0].emit({type:'line',line:'{"from":"a1","to":"a1"}'});
  assert.equal(a.read('turnDeadline'),deadline);assert.equal(a.read('game.history.length'),0);
  a.clock.ms=2600;a.workers[0].emit({type:'line',line:'{"from":"a1","to":"a2"}'});
  assert.match(a.nodes.status.textContent,/quá thời gian/);assert.equal(a.read('game.history.length'),0);assert(a.workers.every(w=>w.terminated));
});
test('stop closes both workers and cancels startup and turn deadlines',async()=>{
  const a=app();await a.nodes.start.onclick();a.ready();a.nodes.stop.onclick();
  assert(a.workers.every(w=>w.terminated));assert.equal(a.timers.size,0);assert.equal(a.nodes.start.disabled,false);assert.match(a.nodes.status.textContent,/Đã dừng/);
});
test('configuration import replaces map and all three bots together, invalid import keeps the board',()=>{
  const a=app(),initial=a.read('JSON.stringify(config)'),cfg=JSON.parse(initial);
  delete cfg.bots.advanced;a.nodes['config-input'].value=JSON.stringify(cfg);a.nodes.apply.onclick();
  assert.equal(a.read('JSON.stringify(config)'),initial);assert.match(a.nodes.status.textContent,/Chưa áp dụng/);
  const next=JSON.parse(initial);next.map.name='new-map';next.bots={beginner:'one',intermediate:'two',advanced:'three'};
  a.nodes['config-input'].value=JSON.stringify(next);a.nodes.apply.onclick();
  assert.equal(a.read('config.map.name'),'new-map');assert.equal(a.read('config.bots.advanced'),'three');assert.equal(a.nodes.board.children.length,81);
});
