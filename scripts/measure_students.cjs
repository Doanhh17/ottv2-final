/* Fixed-arrival HTTP probe. Only a matching, synthetic loopback fixture is accepted. */
const fs=require('fs'),http=require('http'),https=require('https'),{performance}=require('perf_hooks');
const opts={};for(let i=2;i<process.argv.length;i+=2)opts[process.argv[i].replace(/^--/,'')]=process.argv[i+1];
const port=Number(opts.port||18765),count=Number(opts.requests||4000),rps=Number(opts.rps||25);
const concurrency=Number(opts.concurrency||64),timeout=Number(opts.timeout||15000),scenario=opts.scenario||'dashboard';
if(![18765,18766,18767].includes(port)||!Number.isInteger(count)||count<1||count>12000||!(rps>0&&rps<=300)||
   !Number.isInteger(concurrency)||concurrency<1||concurrency>256||!['dashboard','find','login'].includes(scenario)||
   !Number.isInteger(timeout)||timeout<1000||timeout>15000||!opts.fixture||!opts.output||fs.existsSync(opts.output))throw Error('Invalid bounds or existing output');
const fixture=JSON.parse(fs.readFileSync(opts.fixture,'utf8'));
if(fixture.synthetic!==true||fixture.students!==fixture.clients.length||fixture.students>4000)throw Error('Not a synthetic fixture');
const agent=new http.Agent({keepAlive:true,maxSockets:concurrency});
let tunnelIndex=0;
function request(path,client,method='GET',body=null,targetPort=port){return new Promise(resolve=>{
  const started=performance.now(),raw=body===null?null:JSON.stringify(body);
  const headers={Host:'127.0.0.1:18765',Origin:'http://127.0.0.1:18765'};
  if(client){headers.Cookie=client.cookie;headers['X-CSRF-Token']=client.csrf;}
  if(raw!==null){headers['Content-Type']='application/json';headers['Content-Length']=Buffer.byteLength(raw);}
  let done=false;function finish(status,data){if(done)return;done=true;resolve({status,data,seconds:(performance.now()-started)/1000});}
  const tunnelPorts=opts.tunnels==='4'&&targetPort===18765?[18765,18768,18769,18770]:[targetPort];
  const requestPort=tunnelPorts[tunnelIndex++%tunnelPorts.length];
  const req=http.request({hostname:'127.0.0.1',port:requestPort,path,method,headers,agent},res=>{
    let text='';res.setEncoding('utf8');res.on('data',x=>{text+=x;if(text.length>262144)req.destroy(Error('oversize'));});
    res.on('end',()=>{let data;try{data=JSON.parse(text);}catch{data={error:'non_json'};}finish(res.statusCode,data);});
    res.on('error',()=>finish('network_error',{}));
  });
  req.setTimeout(timeout,()=>req.destroy(Error('timeout')));
  req.on('error',err=>finish(err.message==='timeout'?'timeout':'network_error',{error:err.code||err.message}));
  if(raw!==null)req.write(raw);req.end();
});}
function percentile(values,p){if(!values.length)return null;const s=[...values].sort((a,b)=>a-b);return s[Math.ceil(s.length*p)-1];}
async function main(){
  const before=await request('/_loadtest');
  if(before.status!==200||before.data.fixture_id!==fixture.fixture_id)throw Error('Target is not this synthetic fixture');
  console.log(JSON.stringify({state:'load_started',scenario,count,rps,concurrency,students:fixture.students,port}));
  let scheduled=0,started=0,active=0,dropped=0,complete=0,errors=[],maxActive=0,maxLag=0;
  const statuses={},times=[],okTimes=[],users=new Set(),successfulUsers=new Set();
  const health=[];let probing=false,healthFailures=0,abort=false;
  function probe(){if(probing)return;probing=true;const t=performance.now();
    const req=https.get('https://ottv2-nhom12.koreacentral.cloudapp.azure.com/health',res=>{
      res.resume();res.on('end',()=>{health.push({status:res.statusCode,seconds:(performance.now()-t)/1000});
        healthFailures=res.statusCode===200?0:healthFailures+1;probing=false;if(healthFailures>=2)abort=true;});});
    req.setTimeout(4000,()=>req.destroy(Error('timeout')));req.on('error',()=>{health.push({status:'network_error',seconds:(performance.now()-t)/1000});
      probing=false;if(++healthFailures>=2)abort=true;});
  }
  probe();const healthTimer=setInterval(probe,5000),t0=performance.now();
  await new Promise(resolve=>{const timer=setInterval(()=>{
    const now=performance.now(),due=Math.min(count,Math.floor((now-t0)*rps/1000)+1);
    while(scheduled<due&&!abort){const index=scheduled++,client=fixture.clients[index%fixture.students];
      maxLag=Math.max(maxLag,now-(t0+index*1000/rps));
      if(active>=concurrency){dropped++;continue;}
      active++;started++;maxActive=Math.max(maxActive,active);users.add(client.identity);
      const login=scenario==='login',path=login?'/api/login':scenario==='find'?'/api/find':'/api/me';
      request(path,client,scenario==='dashboard'?'GET':'POST',login?{identity:client.identity,password:fixture.password}:scenario==='find'?{}:null)
        .then(row=>{let status=row.status;
          if(status===200&&(scenario==='find'?row.data.bot_id!==client.identity:row.data.user?.identity!==client.identity))status='identity_mismatch';
          statuses[status]=(statuses[status]||0)+1;times.push(row.seconds);
          if(status===200){okTimes.push(row.seconds);successfulUsers.add(client.identity);}
          else if(errors.length<8)errors.push({status,message:row.data.error||null});
          active--;complete++;
        });
    }
    if((scheduled>=count||abort)&&active===0){clearInterval(timer);resolve();}
  },2);});
  clearInterval(healthTimer);const elapsed=(performance.now()-t0)/1000;
  const after=await request('/_loadtest',null,'GET',null,18765);
  const result={measured_at_utc:new Date().toISOString(),synthetic_students:fixture.students,scenario,port,
    planned_requests:count,scheduled,started,completed:complete,dropped_before_start:dropped,not_scheduled:count-scheduled,
    offered_rps:rps,request_timeout_ms:timeout,concurrency_limit:concurrency,max_inflight:maxActive,ssh_tunnels:opts.tunnels==='4'?4:1,elapsed_seconds:elapsed,
    achieved_rps:complete/elapsed,success_rps:okTimes.length/elapsed,status_counts:statuses,
    distinct_users_started:users.size,distinct_users_succeeded:successfulUsers.size,
    latency:{p50_seconds:percentile(times,.5),p95_seconds:percentile(times,.95),max_seconds:Math.max(0,...times)},
    successful_latency:{p50_seconds:percentile(okTimes,.5),p95_seconds:percentile(okTimes,.95),max_seconds:Math.max(0,...okTimes)},
    generator_max_schedule_lag_ms:maxLag,errors,server_before:before.data,server_after:after.data,
    production_health_checks:health,aborted_for_production_health:abort,
    scope:'Loopback staging on VPS via SSH; seeded synthetic sessions/READY. Login separately performs real password hashing. No bot execution or Google sync.'};
  fs.writeFileSync(opts.output,JSON.stringify(result,null,2)+'\n',{flag:'wx'});
  agent.destroy();console.log(JSON.stringify(result));
}
main().catch(error=>{agent.destroy();console.error(error.message);process.exitCode=1;});
