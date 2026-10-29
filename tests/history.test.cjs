const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const os = require('node:os');
const {spawn} = require('node:child_process');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'web/history.html'), 'utf8');
const code = html.match(/<script id="history-code">([\s\S]*?)<\/script>/)[1];
const replayHtml = fs.readFileSync(path.join(root, 'replay/index.html'), 'utf8');
const sampleText = JSON.stringify(require("./replay_fixture.cjs"));
const sample = JSON.parse(sampleText);
const id = sample.match_id;
const summary = {match_id:id, created_at_utc:sample.created_at_utc, bots:sample.bots, result:sample.result};
class Element {
  constructor(tag) { this.tagName=tag; this.children=[]; this.listeners={}; this.attributes={}; this.textContent=''; this.value=''; this.hidden=true; }
  append(...items) { this.children.push(...items); }
  replaceChildren() { this.children=[]; }
  setAttribute(key, value) { this.attributes[key]=value; }
  addEventListener(name, callback) { this.listeners[name]=callback; }
  async click() { if (this.listeners.click) await this.listeners.click(); }
  focus() { this.focused=true; }
  select() { this.selected=true; }
}
function response(value, status=200, length=null) {
  const text = typeof value==='string' ? value : JSON.stringify(value);
  return {ok:status===200, status, headers:{get:()=>length}, text:async()=>text};
}
const settle = () => new Promise(resolve=>setImmediate(resolve));
function app(options={}) {
  const nodes={};
  for (const id of ['refresh','status','matches','manual','log-text']) nodes[id]=new Element('div');
  const calls=[], writes=[], timers=new Map(); let timerId=0;
  const context={TextEncoder, TextDecoder, AbortController, TypeError, SyntaxError,
    document:{getElementById:id=>nodes[id], createElement:tag=>new Element(tag)},
    navigator:options.clipboardMissing ? {} : {clipboard:{writeText:async text=>{
      if(options.clipboardDenied) throw Error('denied'); writes.push(text);
    }}},
    fetch:async (url,settings)=>{
      calls.push({url,settings});
      if(options.fetch) return options.fetch(url,settings);
      return url==='/matches' ? response({matches:options.matches || [summary],skipped_count:0,limited:false}) : response(options.log || sampleText);
    },
    setTimeout:callback=>{timers.set(++timerId,callback);return timerId;}, clearTimeout:id=>timers.delete(id)};
  vm.runInNewContext(code,context);
  return {nodes,calls,writes,timers};
}
function button(app,index=0) { return app.nodes.matches.children[index].children[1]; }
test('initial list renders at most ten summaries and never fetches logs or polls',async()=>{
  const a=app({matches:Array.from({length:10},(_,i)=>({...summary,match_id:i.toString(16).padStart(32,'0')}))});
  await settle();
  assert.equal(a.nodes.matches.children.length,10);
  assert.deepEqual(a.calls.map(c=>c.url),['/matches']);
  assert.equal(a.calls[0].settings.cache,'no-store');
  assert.equal(a.nodes.refresh.disabled,false);
  assert.equal(a.nodes.manual.hidden,true);
  assert.equal(a.timers.size,0);
});
test('one click copies the exact log, usable by the unchanged offline replay',async()=>{
  const a=app();await settle();await button(a).click();
  assert.deepEqual(a.calls.map(c=>c.url),['/matches','/matches/'+id]);
  assert.deepEqual(a.writes,[sampleText]);
  assert.match(a.nodes.status.textContent,/Đã sao chép/);
  assert.equal(a.nodes.manual.hidden,true);
  const ctx={TextEncoder,structuredClone};
  vm.runInNewContext(replayHtml.match(/<script id="replay-code">([\s\S]*?)<\/script>/)[1],ctx);
  const replay=ctx.OTTReplay.parseRecord(a.writes[0]);
  assert.equal(replay.snapshots.length,sample.history.length+1);
  assert.deepEqual(JSON.parse(JSON.stringify(replay.snapshots[0])),sample.initial_state);
  assert.deepEqual(JSON.parse(JSON.stringify(replay.snapshots.at(-1))),sample.final_state);
  assert.equal(replay.clocks.length,sample.clock_history.length);
});
test('clipboard denied or missing exposes selected exact text without fetching twice',async()=>{
  for(const options of [{clipboardDenied:true},{clipboardMissing:true}]) {
    const a=app(options);await settle();await button(a).click();
    assert.equal(a.nodes.manual.hidden,false);
    assert.equal(a.nodes['log-text'].value,sampleText);
    assert.equal(a.nodes['log-text'].selected,true);
    assert.equal(a.writes.length,0);
    assert.equal(a.calls.length,2);
    await a.nodes.refresh.click();
    assert.equal(a.nodes.manual.hidden,true);
    assert.equal(a.nodes['log-text'].value,'');
  }
});
test('log failures never claim success or replace the clipboard',async()=>{
  for(const value of [response('{}',404),response('{}',409),response('{bad'),response({...sample,match_id:'b'.repeat(32)})]) {
    const a=app({fetch:url=>url==='/matches' ? response({matches:[summary]}) : value});
    await settle();await button(a).click();
    assert.match(a.nodes.status.className,/error/);
    assert.equal(a.writes.length,0);
    assert.equal(a.nodes.manual.hidden,true);
    assert.equal(button(a).disabled,false);
  }
});
test('list errors, invalid IDs, excess rows and empty lists are handled',async()=>{
  for(const value of [response('{bad'),response({},500),response({matches:[{...summary,match_id:'../outside'}]}),response({matches:Array(11).fill(summary)})]) {
    const a=app({fetch:()=>value});await settle();
    assert.match(a.nodes.status.className,/error/);
    assert.equal(a.calls.length,1);
    assert.equal(a.nodes.matches.children.length,0);
    assert.equal(a.nodes.refresh.disabled,false);
  }
  const a=app({matches:[]});await settle();assert.match(a.nodes.status.textContent,/Chưa có trận/);
});
test('labels are text nodes and fetched URLs are built from validated IDs',async()=>{
  const label='<img src=x onerror=alert(1)>';
  const a=app({matches:[{...summary,bots:{A:label,B:'guardian'},log_url:'https://evil.example'}]});
  await settle();
  const team=a.nodes.matches.children[0].children[0].children[0].children[0];
  assert.equal(team.children[1].textContent,label);
  assert.equal(team.children[1].children.length,0);
  await button(a).click();assert.equal(a.calls[1].url,'/matches/'+id);
});
test('oversize logs are bounded by headers, text fallback and streaming cancellation',async()=>{
  let cancelled=false;
  const oversized=[response(sampleText,200,String(2*1024*1024+1)), response(' '.repeat(2*1024*1024+1)),
    {...response(''),body:{getReader:()=>({read:async()=>({done:false,value:new Uint8Array(2*1024*1024+1)}),cancel:async()=>{cancelled=true;},releaseLock(){}})}}];
  for(const value of oversized) {
    const a=app({fetch:url=>url==='/matches' ? response({matches:[summary]}) : value});await settle();await button(a).click();
    assert.match(a.nodes.status.textContent,/vượt giới hạn/);assert.equal(a.writes.length,0);
  }
  assert.equal(cancelled,true);
});
test('duplicate clicks and refresh cannot race an in-flight copy',async()=>{
  let complete;
  const a=app({fetch:url=>url==='/matches' ? response({matches:[summary]}) : new Promise(resolve=>{complete=resolve;})});
  await settle();const first=button(a).click();await settle();
  assert.equal(button(a).disabled,true);assert.equal(a.nodes.refresh.disabled,true);
  await button(a).click();await a.nodes.refresh.click();assert.equal(a.calls.length,2);
  complete(response(sampleText));await first;
  assert.equal(a.writes.length,1);assert.equal(a.nodes.refresh.disabled,false);
});
test('network timeout clears busy state and allows a deliberate retry',async()=>{
  const a=app({fetch:(url,settings)=>new Promise((resolve,reject)=>{
    settings.signal.addEventListener('abort',()=>{const error=Error('timeout');error.name='AbortError';reject(error);});
  })});
  await settle();for(const callback of a.timers.values()) callback();await settle();
  assert.match(a.nodes.status.textContent,/Chờ quá lâu/);
  assert.equal(a.nodes.refresh.disabled,false);assert.equal(a.timers.size,0);
});
test('real HTTP page and API feed the copy handler and offline replay parser', {timeout:10000}, async()=>{
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'ottv2-history-'));
  const store=path.join(directory,'store'),matches=path.join(directory,'matches');
  fs.mkdirSync(store);fs.mkdirSync(matches);
  fs.writeFileSync(path.join(matches,id+'.json'),sampleText);
  const source='import sys; from ottv2.local_api import create_server; s=create_server(sys.argv[1],0,sys.argv[2]); print(s.server_port,flush=True); s.serve_forever()';
  const child=spawn(process.platform==='win32'?'python':'python3',['-u','-c',source,store,matches],{cwd:root,stdio:['ignore','pipe','pipe']});
  let errors='';child.stderr.on('data',chunk=>{errors+=chunk;});
  try {
    const port=await new Promise((resolve,reject)=>{
      let output='';const timeout=setTimeout(()=>reject(Error('API did not start: '+errors)),4000);
      child.once('error',error=>{clearTimeout(timeout);reject(error);});
      child.once('exit',code=>{clearTimeout(timeout);reject(Error('API exited '+code+': '+errors));});
      child.stdout.on('data',chunk=>{output+=chunk;if(output.includes('\n')){clearTimeout(timeout);resolve(Number(output.trim()));}});
    });
    const base='http://127.0.0.1:'+port;
    const page=await fetch(base+'/');assert.equal(page.status,200);
    assert.equal(await page.text(),html);
    const a=app({fetch:(url,settings)=>fetch(base+url,settings)});
    // Wait for actual network completion, with a bounded deadline.
    const deadline=Date.now()+3000;
    while(a.nodes.refresh.disabled && Date.now()<deadline) await new Promise(resolve=>setTimeout(resolve,10));
    assert.equal(a.nodes.matches.children.length,1,a.nodes.status.textContent);
    await button(a).click();
    assert.equal(a.writes[0],sampleText);
    assert.deepEqual(a.calls.map(call=>call.url),['/matches','/matches/'+id]);
    const ctx={TextEncoder,structuredClone};
    vm.runInNewContext(replayHtml.match(/<script id="replay-code">([\s\S]*?)<\/script>/)[1],ctx);
    const replay=ctx.OTTReplay.parseRecord(a.writes[0]);
    assert.deepEqual(JSON.parse(JSON.stringify(replay.snapshots.at(-1))),sample.final_state);
    assert.equal(fs.readFileSync(path.join(matches,id+'.json'),'utf8'),sampleText);
  } finally {
    if(child.exitCode===null) { const closed=new Promise(resolve=>child.once('close',resolve)); child.kill();await closed; }
    fs.unlinkSync(path.join(matches,id+'.json'));fs.rmdirSync(matches);fs.rmdirSync(store);fs.rmdirSync(directory);
  }
});
