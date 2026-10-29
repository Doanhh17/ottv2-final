const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const vm = require("node:vm");
const {spawnSync} = require("node:child_process");
const root = path.resolve(__dirname,"..");
const html = fs.readFileSync(path.join(root,"replay/index.html"),"utf8");
const code = html.match(/<script id="replay-code">([\s\S]*?)<\/script>/)[1];
const sampleText = JSON.stringify(require('./replay_fixture.cjs'));
const sample = JSON.parse(sampleText);
const N = sample.history.length;
const budget = sample.limits.cpu_budget_seconds;
const budgetText = budget.toFixed(3)+" s";

test('measured test fixture uses the current seven-second fifty-move policy',()=>{
  assert.deepEqual(sample.limits,{cpu_budget_seconds:7,response_limit_seconds:2.5,
    memory_limit_mib_per_bot:64,max_valid_moves:50,move_limit_tiebreak:'piece_count'});
  assert.ok(sample.cleanup_complete&&sample.history.length<=50);
  assert.deepEqual(sample.clock_history[0],{A:7,B:7});
  for(const side of ['A','B'])assert.ok(sample.history.filter(move=>move.side===side).length<=25);
});

test('offline viewer bundles its assets and never makes automatic network requests',()=>{
  assert.doesNotMatch(html,/<script[^>]+src=|<link[^>]+stylesheet|fetch\s*\(|XMLHttpRequest|WebSocket|@import/i);
  const sources=[...html.matchAll(/<img[^>]+src="([^"]+)"/g)].map(match=>match[1]);
  assert.ok(sources.length>0&&sources.every(src=>src.startsWith('data:image/')));
  assert.doesNotMatch(html,/id="sample"|id="sample-record"|Trận mẫu/);
});
const temp = fs.mkdtempSync(path.join(os.tmpdir(),"ottv2-replay-"));
let fixtures;
try {
  const generated = spawnSync(process.platform === "win32" ? "python" : "python3",
    [path.join(root,"scripts/replay_fixtures.py"),"--output",path.join(temp,"fixtures.json")],{encoding:"utf8",cwd:root});
  assert.equal(generated.status,0,generated.stderr);
  fixtures = JSON.parse(fs.readFileSync(path.join(temp,"fixtures.json"),"utf8"));
} finally { fs.rmSync(path.join(temp,"fixtures.json"),{force:true}); fs.rmdirSync(temp); }
function plain(value) { return JSON.parse(JSON.stringify(value)); }
function parser() { const context = {TextEncoder,structuredClone}; vm.runInNewContext(code,context); return context.OTTReplay; }

test("real saved match reconstructs its final board and keeps earlier snapshots",()=>{
  const replay = parser().parseRecord(sampleText);
  assert.equal(replay.snapshots.length,N+1);
  assert.deepEqual(plain(replay.snapshots[0]),sample.initial_state);
  assert.deepEqual(plain(replay.snapshots.at(-1)),sample.final_state);
  assert.notEqual(replay.snapshots[0].board,replay.snapshots[1].board);
});
for (const [name,record] of Object.entries(fixtures)) {
  test("Python Engine fixture: "+name,()=>{
    const replay = parser().parseRecord(JSON.stringify(record));
    assert.deepEqual(plain(replay.snapshots.at(-1)),record.final_state);
    if (name === "bomb") { assert.equal(replay.snapshots[1].board.a2,undefined); assert.deepEqual(plain(replay.snapshots[1].bombs),[]); }
    if (name === "repetition") assert.equal(record.result.reason,"threefold_repetition");
    if (name === "zero") assert.equal(replay.snapshots.length,1);
  });
}
test("capped replay rejects an early draw, changed cap, and a move after the limit",()=>{
  for (const change of [
    r=>r.limits.max_valid_moves=69,
    r=>r.limits.max_valid_moves=71,
    r=>r.limits.max_valid_moves=true,
    r=>{delete r.limits.max_valid_moves;},
    r=>{r.history.push(structuredClone(r.history[0]));r.result.valid_move_count++;},
    r=>{r.history[0].result='draw';r.history[0].reason='move_limit';},
  ]) { const record=structuredClone(fixtures.move_limit);change(record);
    assert.throws(()=>parser().parseRecord(JSON.stringify(record)));
  }
});
test("material result must match surviving pieces and the pinned tiebreak",()=>{
  for (const change of [
    r=>{r.result.winner="B";r.history.at(-1).result="B";},
    r=>{r.result.winner="draw";r.result.reason="move_limit";r.history.at(-1).result="draw";r.history.at(-1).reason="move_limit";},
    r=>{delete r.limits.move_limit_tiebreak;},
    r=>{r.limits.move_limit_tiebreak="score";},
  ]) { const record=structuredClone(fixtures.material_a);change(record);
    assert.throws(()=>parser().parseRecord(JSON.stringify(record)));
  }
});
test("rejects damaged coordinates, impossible moves and capture/bomb mismatches",()=>{
  for (const change of [
    r=>r.history[0].from="j1", r=>r.history[0].side=r.initial_state.turn==='A'?'B':'A', r=>r.history[0].to="i8",
    r=>r.history[0].capture="paper", r=>r.history[0].bomb=true,
    r=>r.initial_state.board.a1.kind="script", r=>r.initial_state.obstacles.push("a1"),
  ]) { const record=structuredClone(sample); change(record); assert.throws(()=>parser().parseRecord(JSON.stringify(record))); }
});
test("rejects malformed JSON, wrong schema, inconsistent count, limits and final state",()=>{
  assert.throws(()=>parser().parseRecord("{oops"));
  for (const change of [
    r=>r.schema_version=2, r=>r.result.valid_move_count=0, r=>r.limits.cpu_budget_seconds="1.5",
    r=>r.final_state.turn=r.final_state.turn==="A"?"B":"A", r=>delete r.final_state.board[Object.keys(r.final_state.board)[0]],
    r=>r.history[0].result="A", r=>r.history[0].reason="goal",
  ]) { const record=structuredClone(sample); change(record); assert.throws(()=>parser().parseRecord(JSON.stringify(record))); }
});
test("accepts BOM and rejects input over 2 MiB",()=>{
  assert.equal(parser().parseRecord("\uFEFF"+sampleText).snapshots.length,N+1);
  assert.throws(()=>parser().parseRecord(" ".repeat(2*1024*1024+1)));
});

class Element {
  constructor(tag="div") {
    this.tagName=tag.toUpperCase(); this.children=[]; this.dataset={}; this.handlers={};
    this.attributes={}; this.className=""; this.value=""; this.hidden=false; this.disabled=false; this._text="";
    this.classList={toggle:(name,on)=>{const s=new Set(this.className.split(" ").filter(Boolean));if(on)s.add(name);else s.delete(name);this.className=Array.from(s).join(" ");}};
  }
  set textContent(value) { this._text=String(value); this.children=[]; }
  get textContent() { return this._text+this.children.map(n=>n.textContent).join(""); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text="";this.children=children; }
  setAttribute(key,value) { this.attributes[key]=value; }
  addEventListener(name,handler) { (this.handlers[name] ||= []).push(handler); }
  async fire(name,extra={}) { for(const handler of this.handlers[name]||[]) await handler({target:this,...extra}); }
}
function app(animated=false,loaded=true) {
  const nodes={};
  for(const match of html.matchAll(/<([a-z]+)[^>]*\bid="([^"]+)"/g)) nodes[match[2]]=new Element(match[1]);
  const events={}, timers=new Map(), frames=new Map(), delays=[];let id=0;
  const document={getElementById:key=>nodes[key],createElement:tag=>new Element(tag),
    createElementNS:(_,tag)=>new Element(tag),
    addEventListener:(name,fn)=>{events[name]=fn;}};
  const context={document,TextEncoder,structuredClone,
    setInterval:(fn,delay)=>{delays.push(delay);timers.set(++id,fn);return id;},clearInterval:key=>timers.delete(key)};
  if (animated) { context.requestAnimationFrame=fn=>{frames.set(++id,fn);return id;};context.cancelAnimationFrame=key=>frames.delete(key); }
  vm.runInNewContext(code,context);
  if (loaded) { nodes["json-input"].value=sampleText; nodes["load-paste"].fire("click"); }
  return {nodes,events,timers,delays,tick:()=>{for(const fn of Array.from(timers.values()))fn();},
    frame:now=>{const callbacks=Array.from(frames.values());frames.clear();for(const fn of callbacks)fn(now);}};
}
test("loaded JSON has 81 board squares and usable boundaries",()=>{
  const {nodes}=app();
  assert.equal(nodes.board.children.filter(n=>n.dataset.square).length,81);
  assert.equal(nodes["step-label"].textContent,"Nước "+(0)+" / "+N);
  assert.equal(nodes.result.textContent,sample.result.winner+" thắng");
  assert.equal(nodes.first.disabled,true);assert.equal(nodes.next.disabled,false);
  assert.equal(nodes.moves.children.length,N);
  assert.match(nodes.metrics.textContent,/CPU đã dùng/);
});
test("next, previous, last, first and move list choose the correct snapshots",async()=>{
  const {nodes}=app();
  await nodes.next.fire("click");assert.equal(nodes["step-label"].textContent,"Nước "+(1)+" / "+N);
  assert.ok(nodes["move-detail"].textContent.includes(sample.history[0].from+" → "+sample.history[0].to));
  await nodes.last.fire("click");assert.equal(nodes["step-label"].textContent,"Nước "+(N)+" / "+N);
  assert.equal(nodes.next.disabled,true);assert.ok(nodes["move-detail"].textContent.includes(sample.history.at(-1).from+" → "+sample.history.at(-1).to));
  await nodes.previous.fire("click");assert.equal(nodes["step-label"].textContent,"Nước "+(N-1)+" / "+N);
  await nodes.moves.children[9].fire("click");assert.equal(nodes["step-label"].textContent,"Nước "+(10)+" / "+N);
  await nodes.first.fire("click");assert.equal(nodes["step-label"].textContent,"Nước "+(0)+" / "+N);
});
test("slider, keyboard and autoplay navigate without fabricating clocks",async()=>{
  const {nodes,events,timers,tick}=app();
  nodes.timeline.value="12";await nodes.timeline.fire("input");assert.match(nodes["step-label"].textContent,new RegExp((12)+" / "+N));
  events.keydown({key:"ArrowRight",target:{tagName:"BODY"},preventDefault(){}});assert.match(nodes["step-label"].textContent,new RegExp((13)+" / "+N));
  events.keydown({key:"ArrowRight",target:{tagName:"TEXTAREA"},preventDefault(){throw Error("editing must be left alone");}});
  await nodes.play.fire("click");tick();assert.match(nodes["step-label"].textContent,new RegExp((14)+" / "+N));
  await nodes.play.fire("click");assert.equal(timers.size,0);
  await nodes.last.fire("click");await nodes.play.fire("click");
  for(let i=0;i<N;i++)tick();
  assert.equal(timers.size,0);assert.match(nodes["step-label"].textContent,new RegExp((N)+" / "+N));
});
test("bad pasted JSON preserves the current replay and valid JSON clears the error",async()=>{
  const {nodes}=app();await nodes.next.fire("click");
  nodes["json-input"].value="{oops";await nodes["load-paste"].fire("click");
  assert.equal(nodes.error.hidden,false);assert.match(nodes.error.textContent,/JSON không hợp lệ/);
  assert.match(nodes["step-label"].textContent,new RegExp((1)+" / "+N));
  nodes["json-input"].value=sampleText;await nodes["load-paste"].fire("click");assert.equal(nodes.error.hidden,true);assert.match(nodes["step-label"].textContent,new RegExp((0)+" / "+N));
});

test("playback speed and space key change viewing only; piece counts follow the selected position",async()=>{
  const {nodes,events,timers,delays,tick}=app();
  assert.equal(nodes['pieces-a'].textContent,'9 quân');
  assert.equal(nodes['pieces-b'].textContent,'9 quân');
  nodes['playback-speed'].value='2';await nodes.play.fire('click');assert.equal(delays.at(-1),350);
  tick();const selected=nodes['step-label'].textContent;
  nodes['playback-speed'].value='4';await nodes['playback-speed'].fire('change');
  assert.equal(delays.at(-1),175);assert.equal(nodes['step-label'].textContent,selected);assert.equal(timers.size,1);
  events.keydown({key:' ',target:{tagName:'BODY'},preventDefault(){}});assert.equal(timers.size,0);
  await nodes.last.fire('click');
  for(const side of ['A','B'])assert.equal(nodes['pieces-'+side.toLowerCase()].textContent,
    Object.values(sample.final_state.board).filter(piece=>piece.side===side).length+' quân');
});
test("file input handles a zero-move result and enforces size limit",async()=>{
  const {nodes}=app();
  nodes.file.files=[{name:"startup.json",size:500,text:async()=>JSON.stringify(fixtures.zero)}];
  await nodes.file.fire("change");
  assert.equal(nodes["step-label"].textContent,"Nước 0 / 0");
  assert.equal(nodes.play.disabled,true);assert.equal(nodes.next.disabled,true);
  assert.match(nodes["move-detail"].textContent,/Trận kết thúc trước nước đầu/);
  nodes.file.files=[{name:"large.json",size:2*1024*1024+1,text:()=>{throw Error("must not read large file");}}];
  await nodes.file.fire("change");assert.equal(nodes.error.hidden,false);assert.match(nodes.error.textContent,/2 MiB/);
});
test("bot names and match IDs are rendered as plain text",async()=>{
  const {nodes}=app(), record=structuredClone(sample);
  record.bots.A='<img src=x onerror="alert(1)">';record.match_id="<script>alert(1)</script>";
  nodes["json-input"].value=JSON.stringify(record);await nodes["load-paste"].fire("click");
  assert.equal(nodes.error.hidden,true);
  assert.match(nodes["match-meta"].textContent,/<img/);
  assert.equal(nodes["match-meta"].children.length,0);
});
test("real per-move CPU clocks start at budget, freeze the other side and reach final readings",()=>{
  const api=parser(), replay=api.parseRecord(sampleText);
  assert.equal(replay.clocks.length,N+1);
  assert.deepEqual(plain(api.clockAt(replay,0)),{A:budget,B:budget});
  for(let index=1;index<replay.clocks.length;index++){
    const before=replay.clocks[index-1],after=replay.clocks[index];
    const other=sample.history[index-1].side==="A"?"B":"A";
    assert.equal(before[other],after[other]);
    assert.ok(after.A<=before.A&&after.B<=before.B);
  }
  assert.deepEqual(plain(api.clockAt(replay,N)),sample.final_clock);
});
test("damaged or incomplete clock traces are rejected instead of guessed",()=>{
  for(const change of [
    r=>r.clock_history.pop(),r=>delete r.final_clock,r=>r.clock_history[0].A=1,
    r=>r.clock_history[1].B=1,r=>r.clock_history[2].A=budget+0.1,r=>r.final_clock.A=-1,
    r=>r.final_clock.B=budget,r=>r.clock_history[1].A="1.4"
  ]) { const record=structuredClone(sample);change(record);assert.throws(()=>parser().parseRecord(JSON.stringify(record))); }
});
test("old logs have no intermediate clocks and keep only their measured final clock",async()=>{
  const old=structuredClone(sample);delete old.clock_history;delete old.final_clock;
  const api=parser(),replay=api.parseRecord(JSON.stringify(old));
  assert.equal(api.clockAt(replay,0),null);
  assert.equal(api.clockAt(replay,N).A,old.resources.A.cpu_remaining_seconds);
  const {nodes}=app();nodes["json-input"].value=JSON.stringify(old);await nodes["load-paste"].fire("click");
  assert.equal(nodes["clock-a"].textContent,"—");assert.match(nodes["clock-info"].textContent,/Chỉ có số đo CPU cuối trận/);
  await nodes.last.fire("click");assert.equal(nodes["clock-a"].textContent,old.resources.A.cpu_remaining_seconds.toFixed(3)+" s");
});
test("player clocks follow next, last and rewind while labels stay outside the pieces",async()=>{
  const {nodes}=app();
  assert.equal(nodes["clock-a"].textContent,budgetText);
  assert.equal(nodes["clock-b"].textContent,budgetText);
  assert.match(nodes['player-'+sample.initial_state.turn.toLowerCase()].className,/active/);
  await nodes.next.fire("click");
  assert.equal(nodes["clock-a"].textContent,sample.clock_history[1].A.toFixed(3)+" s");
  assert.match(nodes['player-'+(sample.initial_state.turn==='A'?'b':'a')].className,/active/);
  const tokens=nodes.board.children.flatMap(n=>n.children).filter(n=>n.className.startsWith("piece "));
  assert.equal(tokens.length,Object.keys(sample.initial_state.board).length);assert.ok(tokens.every(n=>n.textContent===""&&n.children[0].tagName==="SVG"));
  await nodes.last.fire("click");assert.equal(nodes["clock-b"].textContent,sample.final_clock.B.toFixed(3)+" s");
  await nodes.first.fire("click");assert.equal(nodes["clock-a"].textContent,budgetText);
});
test("clock transition interpolates between measured endpoints and finishes exactly",async()=>{
  const {nodes,frame}=app(true);
  await nodes.last.fire("click");frame(0);frame(90);
  const halfway=Number.parseFloat(nodes["clock-a"].textContent);
  assert.ok(halfway<=budget&&halfway>=sample.final_clock.A-0.0005);
  frame(180);assert.equal(nodes["clock-a"].textContent,sample.final_clock.A.toFixed(3)+" s");
  await nodes.first.fire("click");frame(200);frame(380);
  assert.equal(nodes["clock-a"].textContent,budgetText);
});

 test("empty viewer shows a board without an invented result or clocks",async()=>{
  const {nodes}=app(false,false);
  assert.equal(nodes.board.children.filter(n=>n.dataset.square).length,81);
  assert.equal(nodes.result.textContent,"Bàn cờ ban đầu");
  assert.equal(nodes["clock-a"].textContent,"—");
  assert.match(nodes.limits.textContent,/7 giây.*25 nước/);
  for(const id of ["first","previous","next","last","play","timeline"]) assert.equal(nodes[id].disabled,true);
  await nodes.next.fire("click");await nodes.play.fire("click");await nodes.last.fire("click");
  assert.equal(nodes.result.textContent,"Bàn cờ ban đầu");
});
test("legacy JSON is labelled as old rules without rewriting its CPU budget",async()=>{
  const {nodes}=app();const old=structuredClone(sample);old.limits.cpu_budget_seconds=10;
  old.clock_history=old.clock_history.map(clock=>({A:clock.A+3,B:clock.B+3}));
  old.final_clock={A:old.final_clock.A+3,B:old.final_clock.B+3};
  for(const side of ["A","B"])old.resources[side].cpu_remaining_seconds+=3;
  nodes["json-input"].value=JSON.stringify(old);await nodes["load-paste"].fire("click");
  assert.equal(nodes.error.hidden,true);assert.match(nodes.limits.textContent,/Giới hạn khác luật hiện hành: CPU 10/);
  assert.equal(nodes["clock-a"].textContent,"10.000 s");
});
