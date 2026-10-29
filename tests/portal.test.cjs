const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../web/portal.html'),'utf8');
const code=html.match(/<script id="portal-code">([\s\S]*?)<\/script>/)[1];
const replay=fs.readFileSync(path.join(__dirname,'../replay/index.html'),'utf8');
const log=require('./replay_fixture.cjs');
const base={user:{identity:'student01',display_name:'Bạn Một',role:'student',repository:'https://github.com/example/bot',csrf:'token'},map:{name:'open'},paused:false,pause_reason:'',blocked:false,updates_locked:false,
  quota:{active:{remaining:2,used:1,reserved:0},passive:{remaining:3,used:0,reserved:0}},request:{state:'idle'},bot:{version:'a'.repeat(64)},update:null,cooldown_seconds:0,elo:1016,
  matches:[{match_id:log.match_id,state:'completed',created_at_utc:log.created_at_utc,map:'open',players:{A:'student01',B:'student02'},result:log.result,log_available:true}]};
class Element{
  constructor(tag){this.tagName=tag;this.textContent='';this.value='';this.hidden=false;this._disabled=false;this.disabledWrites=[];this.sourceWrites=0;this.listeners={};this.children=[];}
  get disabled(){return this._disabled;}set disabled(value){this._disabled=value;this.disabledWrites.push(value);}
  get src(){return this._src;}set src(value){this._src=value;this.sourceWrites++;}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(){this.children=[];}
  addEventListener(name,fn){this.listeners[name]=fn;}
  async click(){if(this.listeners.click)return this.listeners.click();}
  async submit(){if(this.listeners.submit)return this.listeners.submit({preventDefault(){}});}
  focus(){this.focused=true;} select(){this.selected=true;}
}
const reply=(body,status=200)=>({ok:status===200,status,headers:{get(){return null;}},text:async()=>typeof body==='string'?body:JSON.stringify(body)});
const settle=()=>new Promise(resolve=>setImmediate(resolve));
function app(options={}){
  const nodes={},all=[],calls=[],writes=[],timers=new Map(),delays=new Map();let timer=0;
  for(const match of html.matchAll(/<([a-z]+)\b[^>]*\bid="([^"]+)"[^>]*>/g)){const node=new Element(match[1]);node.hidden=match[0].includes(' hidden');nodes[match[2]]=node;all.push(node);}
  const document={getElementById:id=>nodes[id],createElement:tag=>{const node=new Element(tag);all.push(node);return node;},querySelectorAll:selector=>all.filter(node=>node.tagName===selector),activeElement:null};
  const context={document,TextEncoder,TextDecoder,AbortController,TypeError,Date,location:{pathname:options.route||'/arena'},
    navigator:{clipboard:{writeText:async text=>{if(options.denied)throw Error('denied');writes.push(text);}}},
    setTimeout:(fn,delay)=>{timers.set(++timer,fn);delays.set(timer,delay);return timer;},clearTimeout:id=>{timers.delete(id);delays.delete(id);},
    fetch:async(url,settings)=>{calls.push({url,settings});if(options.fetch)return options.fetch(url,settings);return reply(['/api/me','/api/status'].includes(url)?structuredClone(options.data||base):url.startsWith('/api/logs/')?log:{ok:true});}};
  vm.runInNewContext(code,context);return{nodes,all,calls,writes,timers,delays,document,
    async tick(){const [id,fn]=timers.entries().next().value;timers.delete(id);await fn();}};
}
const copyButton=a=>a.nodes.matches.children[0].children[1];

test('task documentation is offered before the related controls',()=>{
  for(const [start,end,action,label] of [
    ['id="bot-page"','id="results-page"','id="update"','Đọc hướng dẫn viết bot'],
    ['id="results-page"','id="guide-page"','id="matches"','Tải replay offline'],
    ['id="admin-controls"','id="admin-students"','id="pause-reason"','Hướng dẫn bản đồ JSON'],
  ]){
    const section=html.slice(html.indexOf(start),html.indexOf(end));
    assert.ok(section.indexOf(label)>0&&section.indexOf(label)<section.indexOf(action));
  }
});

test('student starts with one summary request and automatic polling and visible quota',async()=>{
  const a=app();await settle();assert.deepEqual(a.calls.map(x=>x.url),['/api/me']);assert.equal(a.timers.size,1);
  assert.equal(a.nodes['active-left'].textContent,'Còn 2 lượt');assert.equal(a.nodes['student-panel'].hidden,false);assert.equal(a.nodes['admin-panel'].hidden,true);
  assert.equal(a.nodes.find.disabled,false);assert.equal(a.nodes.cancel.disabled,true);assert.equal(a.nodes.matches.children.length,1);
  assert.equal(a.nodes['public-links'].hidden,true);
});

test('anonymous visitor can download practice and replay without exposing private match data',async()=>{
  const a=app({fetch:()=>reply({error:'Bạn cần đăng nhập.'},401)});await settle();
  assert.equal(a.nodes['login-panel'].hidden,false);assert.equal(a.nodes.workspace.hidden,true);
  assert.equal(a.nodes['public-links'].hidden,false);assert.equal(a.nodes.nav.hidden,true);
  const publicNav=html.match(/<nav id="public-links"[^>]*>([\s\S]*?)<\/nav>/)[1];
  assert.equal((publicNav.match(/<a /g)||[]).length,1);
  assert.doesNotMatch(publicNav,/docs.google.com|Bảng xếp hạng/);
  assert.match(html,/<img id="public-board"[^>]*src="\/board\.svg"/);
  assert.match(html,/<img src="\/assets\/uet-logo\.png"/);
});

test('operator sees connected and revoked workers as plain text',async()=>{
  const data={...structuredClone(base),user:{...base.user,role:'admin'},admin:{waiting:0,busy_players:0,control:{blocked_bots:{}}},students:[],failures:[],cluster:{mode:'distributed',workers:[
    {worker_id:'worker01',enabled:1,online:true,match_id:'abc',last_error:''},
    {worker_id:'worker02',enabled:0,online:false,last_error:'<script>text only</script>'}
  ]}};
  const a=app({data,route:'/system'});await settle();
  assert.equal(a.nodes.workers.children.length,2);
  assert.match(a.nodes.workers.children[0].textContent,/worker01.*Đang chấm/);
  assert.match(a.nodes.workers.children[1].textContent,/Đã thu hồi khóa/);
  assert.match(a.nodes.workers.children[1].textContent,/<script>text only<\/script>/);
  assert.equal(a.nodes['admin-failures'].hidden,false);
});

test('each student route shows one task and excludes operator sections',async()=>{
  for(const route of ['arena','bot','results','guide','account']){
    const a=app({route:'/'+route});await settle();
    for(const page of ['arena','bot','results','guide','account'])assert.equal(a.nodes[page+'-page'].hidden,page!==route);
    assert.equal(a.nodes['admin-panel'].hidden,true);
    assert.equal(a.nodes.nav.children.length,6);
  }
  const denied=app({route:'/students'});await settle();
  assert.equal(denied.nodes['arena-page'].hidden,false);
  assert.equal(denied.nodes['admin-panel'].hidden,true);
});

test('student sees only own Elo and external Sheet and account-change links',async()=>{
  const a=app({route:'/account'});await settle();
  assert.equal(a.nodes['own-elo'].textContent,'Elo 1016');
  assert.equal(a.nodes.ratings,undefined);
  assert.equal(a.nodes['password-form'],undefined);
  assert.equal(a.nodes['repository-form'],undefined);
  const standings=a.nodes.nav.children.find(node=>node.textContent.includes('Bảng xếp hạng'));
  assert.equal(standings.href,'/standings');assert.equal(standings.target,'_blank');
  assert.equal(standings.rel,'noopener noreferrer');
  const guides=html.match(/<div id="resource-guides"[^>]*>([\s\S]*?)<\/div>/)[1];
  assert.doesNotMatch(guides,/Bảng xếp hạng|docs.google.com\/spreadsheets/);
  assert.equal((guides.match(/target="_blank"/g)||[]).length,4);
  assert.equal(a.nodes['resource-guides'].hidden,false);
  assert.equal(a.nodes['admin-map-guide'].hidden,true);
  assert.equal(a.nodes.repository.textContent,base.user.repository);
});
test('admin has map documentation, no account page and a production role label',async()=>{
  const data={...structuredClone(base),user:{...base.user,identity:'admin',role:'admin',display_name:'Quản lý thử'},admin:{waiting:0,busy_players:0,control:{blocked_bots:{}}},students:[],failures:[]};
  const a=app({data,route:'/guide'});await settle();
  assert.equal(a.nodes.who.textContent,'Quản trị viên');
  assert.equal(a.nodes.nav.children.some(link=>link.href==='/account'),false);
  assert.equal(a.nodes['admin-map-guide'].hidden,false);assert.equal(a.nodes['resource-guides'].hidden,false);
  assert.doesNotMatch(html,/id="map-example"|id="copy-map-example"/);
  const mapLinks=[...html.matchAll(/<a[^>]+href="([^"]+)"[^>]*>[^<]*(?:<strong>)?Hướng dẫn bản đồ JSON/g)];
  assert.equal(mapLinks.length,2);
  assert.equal(mapLinks[0][1],mapLinks[1][1]);
  assert.match(mapLinks[0][1],/^https:\/\/docs.google.com\/document\/d\//);
  assert.equal(a.nodes.nav.children.find(link=>link.href==='/standings').target,'_blank');
  const denied=app({data,route:'/account'});await settle();
  assert.equal(denied.nodes['account-page'].hidden,true);assert.equal(denied.nodes['admin-controls'].hidden,false);
});

test('unauthenticated page remains usable and sends only login credentials',async()=>{
  let signedIn=false;
  const a=app({fetch:(url,settings)=>{if(url==='/api/login'){signedIn=true;return reply({user:base.user});}return signedIn?reply(base):reply({error:'Bạn cần đăng nhập.'},401);}});
  await settle();assert.equal(a.nodes['login-panel'].hidden,false);assert.equal(a.nodes.workspace.hidden,true);
  a.nodes.identity.value='student01';a.nodes.password.value='my-password-123';await a.nodes['login-form'].submit();
  assert.equal(a.nodes['login-panel'].hidden,true);assert.equal(a.nodes.password.value,'');
  const login=a.calls.find(c=>c.url==='/api/login');assert.deepEqual(JSON.parse(login.settings.body),{identity:'student01',password:'my-password-123'});
});
test('find submits no chosen identity and refreshes state with CSRF',async()=>{
  const a=app();await settle();await a.nodes.find.click();
  const request=a.calls.find(c=>c.url==='/api/find');assert.deepEqual(JSON.parse(request.settings.body),{});
  assert.equal(request.settings.headers['X-CSRF-Token'],'token');assert.equal(request.settings.credentials,'same-origin');assert.equal(a.calls.at(-1).url,'/api/me');
});
test('matched students see ten minute expectation and completed result appears automatically',async()=>{
  let complete=false;
  const pending={...structuredClone(base),request:{state:'matched',job_state:'pending',wait_seconds:20},matches:[{...base.matches[0],state:'pending',wait_seconds:20,result:null,log_available:false}]};
  const a=app({fetch:()=>reply(complete?base:pending)});await settle();
  assert.match(a.nodes['request-state'].textContent,/dự kiến trong 10 phút/);assert.equal(a.timers.size,1);
  assert.equal(a.nodes['wait-hint'].hidden,true);
  assert.match(a.nodes.matches.children[0].children[0].children[1].textContent,/10 phút/);
  complete=true;await a.tick();
  assert.match(a.nodes.status.textContent,/Đã chấm xong/);assert.equal(a.timers.size,1);
  assert.equal(a.nodes['wait-hint'].hidden,false);
  assert.equal(a.calls.filter(row=>row.url==='/api/me').length,1);
  assert.equal(a.calls.filter(row=>row.url==='/api/status').length,1);
});
test('overdue jobs get a delay notice without another find request',async()=>{
  const a=app({data:{...base,request:{state:'matched',job_state:'running',wait_seconds:601},matches:[{...base.matches[0],state:'running',wait_seconds:601,result:null,log_available:false}]}});await settle();
  assert.match(a.nodes['request-state'].textContent,/Máy chấm đang chậm/);
  assert.match(a.nodes['request-state'].textContent,/không cần tìm lại/);
  assert.match(a.nodes.matches.children[0].children[0].children[1].textContent,/Máy chấm đang chậm/);
  await a.tick();assert.equal(a.calls.filter(row=>row.url==='/api/find').length,0);
});

test('status polling is light and fetches history only at completion',async()=>{
  let complete=false;
  const pending={...structuredClone(base),request:{state:'matched',match_id:log.match_id,job_state:'pending',wait_seconds:5},matches:[{...base.matches[0],state:'pending',result:null,log_available:false}]};
  const a=app({fetch:url=>{
    if(url==='/api/me')return reply(pending);
    if(url==='/api/history')return reply({matches:base.matches});
    if(url==='/api/status'){const {matches,...data}=structuredClone(complete?base:pending);return reply({...data,latest_match:{match_id:log.match_id,state:complete?'completed':'pending'}});}
    throw Error('unexpected endpoint '+url);
  }});
  await settle();await a.tick();
  assert.equal(a.calls.filter(r=>r.url==='/api/history').length,0);
  complete=true;await a.tick();
  assert.equal(a.calls.filter(r=>r.url==='/api/history').length,1);
  assert.equal(a.calls.filter(r=>r.url==='/api/me').length,1);
  assert.equal(a.timers.size,1);
  assert.match(a.nodes.status.textContent,/Đã chấm xong/);
});
test('waiting for opponent and passive match history both refresh automatically',async()=>{
  for(const data of [{...base,request:{state:'waiting'}},{...base,matches:[{...base.matches[0],state:'pending',result:null,log_available:false}]}]){
    const a=app({data});await settle();assert.equal(a.timers.size,1);await a.tick();assert.equal(a.calls.length,2);
  }
});
test('hidden waiting page makes no polling request and expired session cancels timer',async()=>{
  let expired=false;
  const a=app({fetch:()=>expired?reply({error:'Đăng nhập lại.'},401):reply({...base,request:{state:'waiting'}})});await settle();
  a.document.hidden=true;await a.tick();assert.equal(a.calls.length,1);assert.equal(a.timers.size,1);
  a.document.hidden=false;expired=true;await a.tick();assert.equal(a.nodes.workspace.hidden,true);assert.equal(a.timers.size,0);
});
test('poll failure retries while logout clears all polling',async()=>{
  let fail=false;
  const a=app({fetch:url=>url==='/api/logout'?reply({ok:true}):fail?reply({error:'Máy tạm thời bận'},503):reply({...base,request:{state:'waiting'}})});await settle();
  fail=true;await a.tick();assert.match(a.nodes.status.textContent,/tạm thời bận/);assert.equal(a.timers.size,1);
  await a.nodes.logout.click();assert.equal(a.timers.size,0);assert.equal(a.nodes.workspace.hidden,true);
});
test('paused blocked exhausted and busy views disable inappropriate actions',async()=>{
  for(const change of [{paused:true},{blocked:true},{bot:null},{request:{state:'waiting'}},{quota:{...base.quota,active:{remaining:0,used:10,reserved:0}}}]){
    const a=app({data:{...base,...change}});await settle();assert.equal(a.nodes.find.disabled,true);
  }
  const a=app({data:{...base,updates_locked:true,request:{state:'waiting'}}});await settle();assert.equal(a.nodes.cancel.disabled,false);assert.equal(a.nodes.update.disabled,true);
});
test('copy is lazy, exact and works with the existing offline replay',async()=>{
  const a=app();await settle();await copyButton(a).click();
  assert.deepEqual(a.calls.map(c=>c.url),['/api/me','/api/logs/'+log.match_id]);assert.equal(a.writes[0],JSON.stringify(log));
  const context={TextEncoder,structuredClone};vm.runInNewContext(replay.match(/<script id="replay-code">([\s\S]*?)<\/script>/)[1],context);
  assert.equal(context.OTTReplay.parseRecord(a.writes[0]).snapshots.length,log.history.length+1);
});

test('expired replay keeps result visible without offering a broken copy button',async()=>{
  const message='Bản ghi đã hết hạn lưu 7 ngày. Điểm và kết quả vẫn được giữ.';
  const expired={...base.matches[0],log_available:false,log_expired:true,log_error:message};
  const a=app({data:{...base,matches:[expired]}});await settle();
  const row=a.nodes.matches.children[0];
  assert.equal(row.children.length,1);
  assert.equal(row.children[0].children.at(-1).textContent,message);
  assert(row.children[0].children[1].textContent.includes('thắng')||row.children[0].children[1].textContent.includes('Hòa'));
  assert.equal(a.calls.length,1);
});
test('clipboard refusal selects the same text and does not fetch a second time',async()=>{
  const a=app({denied:true});await settle();await copyButton(a).click();
  assert.equal(a.nodes.manual.hidden,false);assert.equal(a.nodes['manual-text'].selected,true);assert.equal(a.nodes['manual-text'].value,JSON.stringify(log));assert.equal(a.calls.length,2);
});
test('labels render as text, never markup, and invalid match IDs stop rendering',async()=>{
  const dangerous='<img src=x onerror=alert(1)>';
  const a=app({data:{...base,user:{...base.user,display_name:dangerous},matches:[{...base.matches[0],players:{A:dangerous,B:'student02'}}]}});await settle();
  assert.equal(a.nodes.who.textContent,dangerous);assert.equal(a.nodes.matches.children[0].children[0].children[0].children[1].textContent,dangerous);
  const invalid=app({data:{...base,matches:[{...base.matches[0],match_id:'../etc'}]}});await settle();assert.match(invalid.nodes.status.textContent,/Mã trận/);
});
test('server errors are shown without success and expired session hides old results',async()=>{
  const a=app({fetch:url=>url==='/api/me'?reply(base):reply({error:'Hết lượt.'},400)});await settle();await a.nodes.find.click();assert.equal(a.nodes.status.textContent,'Hết lượt.');assert.equal(a.nodes.find.disabled,false);
  const expired=app({fetch:url=>url==='/api/me'?reply(base):reply({error:'Đăng nhập lại.'},401)});await settle();await copyButton(expired).click();assert.equal(expired.nodes.workspace.hidden,true);assert.equal(expired.nodes.matches.children.length,0);
});
test('duplicate click cannot send a second request while first is pending',async()=>{
  let resolve;
  const a=app({fetch:url=>url==='/api/me'?reply(base):new Promise(done=>resolve=done)});await settle();
  const first=a.nodes.find.click();await settle();await a.nodes.find.click();assert.equal(a.calls.filter(c=>c.url==='/api/find').length,1);assert.equal(a.nodes.refresh.disabled,true);resolve(reply({state:'waiting'}));await first;assert.equal(a.nodes.refresh.disabled,false);
});
test('admin tools use their intended endpoints and no student quota is required',async()=>{
  const data={...base,user:{...base.user,identity:'admin',role:'admin'},admin:{waiting:0,busy_players:2,control:{blocked_bots:{student02:'review'}}},students:[base.user],failures:[]};
  const a=app({data});await settle();assert.equal(a.nodes['admin-panel'].hidden,false);assert.equal(a.nodes['student-panel'].hidden,true);a.nodes['pause-reason'].value='Đổi bản đồ';await a.nodes.pause.click();
  assert.deepEqual(JSON.parse(a.calls.find(c=>c.url==='/api/admin/pause').settings.body),{paused:true,reason:'Đổi bản đồ'});
  a.nodes['new-identity'].value=' student04 ';a.nodes['new-name'].value=' Sinh viên mới ';a.nodes['new-repository'].value=' https://github.com/student/bot ';a.nodes['new-password'].value='private-password-123';
  await a.nodes['student-form'].submit();
  assert.deepEqual(JSON.parse(a.calls.find(c=>c.url==='/api/admin/student').settings.body),{identity:'student04',display_name:'Sinh viên mới',repository:'https://github.com/student/bot',password:'private-password-123'});
  assert.equal(a.nodes['new-password'].value,'');
});

test('operator locks and unlocks selected student and sees the current status',async()=>{
  const data={...structuredClone(base),user:{...base.user,identity:'admin',role:'admin'},admin:{waiting:0,busy_players:0,control:{blocked_bots:{}}},students:[{...base.user,locked:false}],failures:[]};
  const a=app({route:'/students',fetch:async(url,settings)=>{
    if(url==='/api/admin/account-lock')data.students[0].locked=JSON.parse(settings.body).locked;
    return reply(url==='/api/me'?structuredClone(data):{ok:true});
  }});await settle();
  assert.equal(a.nodes.block.disabled,false);assert.equal(a.nodes.unblock.disabled,true);
  a.nodes['block-reason'].value='Kiểm tra';await a.nodes.block.click();
  assert.deepEqual(JSON.parse(a.calls.find(c=>c.url==='/api/admin/account-lock').settings.body),{identity:'student01',locked:true,reason:'Kiểm tra'});
  assert.equal(a.nodes.block.disabled,true);assert.equal(a.nodes.unblock.disabled,false);
  assert.match(a.nodes['blocked-list'].textContent,/student01/);
  await a.nodes.unblock.click();
  assert.equal(JSON.parse(a.calls.filter(c=>c.url==='/api/admin/account-lock')[1].settings.body).locked,false);
  assert.equal(a.nodes.block.disabled,false);assert.equal(a.nodes.unblock.disabled,true);
});

test('public copy contains the map and three bots and offers a clipboard fallback',async()=>{
  const bundle={schema_version:1,type:'ottv2_practice_config',map:{name:'current'},bots:{beginner:'one',intermediate:'two',advanced:'three'},limits:{cpu_budget_seconds:7}};
  for(const denied of [false,true]){
    const a=app({denied,fetch:url=>url==='/api/practice-config'?reply(bundle):reply({error:'Đăng nhập'},401)});await settle();
    await a.nodes['copy-practice-public'].click();
    assert.equal(a.calls.at(-1).url,'/api/practice-config');
    const text=denied?a.nodes['practice-copy-text'].value:a.writes[0];assert.deepEqual(JSON.parse(text),bundle);
    if(denied)assert.equal(a.nodes['practice-copy-text'].selected,true);
  }
});

test('unpaused map save is stopped locally with the specific pause instruction',async()=>{
  const a=app({route:'/manage',data:{...base,user:{...base.user,role:'admin'},students:[],admin:{waiting:0,busy_players:0,control:{blocked_bots:{}}},failures:[]}});await settle();
  await a.nodes['map-form'].submit();
  assert.equal(a.calls.filter(c=>c.url==='/api/admin/map').length,0);
  assert.match(a.nodes.status.textContent,/Tạm dừng nhận trận trước/);
});

test('idle online workers appear before busy and disconnected workers',async()=>{
  const data={...base,user:{...base.user,role:'admin'},students:[],admin:{waiting:0,busy_players:0,control:{blocked_bots:{}}},failures:[],cluster:{mode:'distributed',workers:[
    {worker_id:'offline',enabled:1,online:false},{worker_id:'busy',enabled:1,online:true,match_id:'123'},{worker_id:'idle',enabled:1,online:true}
  ]}};
  const a=app({route:'/system',data});await settle();
  assert.deepEqual(a.nodes.workers.children.map(n=>n.textContent.split(' · ')[0]),['idle','busy','offline']);
});

test('background status requests keep controls enabled and preserve unchanged visible content',async()=>{
  let resolve;
  const a=app({fetch:url=>url==='/api/status'?new Promise(done=>resolve=done):reply(base)});await settle();
  const nav=a.nodes.nav.children[0],match=a.nodes.matches.children[0],imageWrites=a.nodes['current-board'].sourceWrites;
  const disabledWrites=a.nodes.find.disabledWrites.length;
  const pending=a.tick();await settle();
  assert.equal(a.nodes.find.disabled,false);assert.equal(a.nodes.refresh.disabled,false);
  assert.equal(a.nodes.nav.children[0],nav);assert.equal(a.nodes.matches.children[0],match);
  resolve(reply(base));await pending;
  assert.equal(a.nodes.nav.children[0],nav);assert.equal(a.nodes.matches.children[0],match);
  assert.equal(a.nodes['current-board'].sourceWrites,imageWrites);
  assert.equal(a.nodes.find.disabledWrites.length,disabledWrites);
});

test('background refresh does not erase selected log text',async()=>{
  const a=app({denied:true});await settle();await copyButton(a).click();
  const text=a.nodes['manual-text'].value;assert.equal(a.nodes.manual.hidden,false);
  await a.tick();assert.equal(a.nodes.manual.hidden,false);assert.equal(a.nodes['manual-text'].value,text);
});

test('a late background response cannot overwrite a newer find action',async()=>{
  let resolve,found=false;
  const newer={...structuredClone(base),quota:{...base.quota,active:{remaining:1,used:1,reserved:1}},request:{state:'matched',job_state:'pending',wait_seconds:0}};
  const a=app({fetch:url=>{
    if(url==='/api/status')return new Promise(done=>resolve=done);
    if(url==='/api/find'){found=true;return reply({state:'matched'});}
    return reply(found?newer:base);
  }});await settle();const pending=a.tick();await settle();
  await a.nodes.find.click();assert.equal(a.nodes['active-left'].textContent,'Còn 1 lượt');
  resolve(reply(base));await pending;
  assert.equal(a.nodes['active-left'].textContent,'Còn 1 lượt');assert.match(a.nodes['request-state'].textContent,/Đã ghép trận/);
});

test('worker heartbeats do not rebuild the unchanged administration view',async()=>{
  let counter=0;
  const a=app({route:'/system',fetch:()=>reply({...base,user:{...base.user,role:'admin'},students:[base.user],admin:{waiting:0,busy_players:0,control:{blocked_bots:{}}},failures:[],cluster:{mode:'distributed',workers:[{worker_id:'idle',enabled:1,online:true,last_seen:++counter}]}})});await settle();
  const worker=a.nodes.workers.children[0],student=a.nodes['student-select'].children[0],nav=a.nodes.nav.children[0],imageWrites=a.nodes['admin-board'].sourceWrites;
  await a.tick();assert.equal(a.nodes.workers.children[0],worker);assert.equal(a.nodes['student-select'].children[0],student);assert.equal(a.nodes.nav.children[0],nav);assert.equal(a.nodes['admin-board'].sourceWrites,imageWrites);
});

test('practice copy controls exist only at login and student arena',()=>{
  assert.deepEqual([...html.matchAll(/id="copy-practice-([a-z]+)"/g)].map(m=>m[1]),['public','arena']);
  const arena=html.slice(html.indexOf('id="arena-page"'),html.indexOf('id="bot-page"'));
  assert.match(arena,/id="copy-practice-arena"/);
});

test('automatic refresh waits about one minute even with one active visitor',async()=>{
 const a=app();await settle();const delay=a.delays.get(a.timers.keys().next().value);
 assert(delay>=60000&&delay<61000);await a.tick();
 assert.equal(a.calls.filter(c=>c.url==='/api/status').length,1);
});
test('anonymous navigation does not duplicate the download controls or expose technical prose',()=>{
 const nav=html.match(/<nav id="public-links"[^>]*>([\s\S]*?)<\/nav>/)[1];
 assert.doesNotMatch(nav,/Đấu thử|Tải replay/);
 const login=html.slice(html.indexOf('id="login-panel"'),html.indexOf('id="workspace"'));
 assert.match(login,/Đấu thử với máy/);assert.match(login,/Tải replay/);assert.doesNotMatch(login,/Isolate|sandbox|CPU\/RAM/);
});
