const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const crypto=require('node:crypto');
const source=fs.readFileSync('google/Code.gs','utf8');

function fixture(){
  const props=new Map([['OTTV2_SECRET','a'.repeat(64)],['OTTV2_VERIFIED_EMAIL','reviewed-verified'],['OTTV2_RESET_VERIFIED_EMAIL','reviewed-verified']]);
  const sheets=new Map(),responses=new Map(),cache=new Map();
  const deleted=[];
  const c={Date,JSON,Map,Set,Number,String,Array,Error,Object,Math,
    PropertiesService:{getScriptProperties:()=>({getProperty:k=>props.get(k),setProperty:(k,v)=>props.set(k,v)})},
    Utilities:{Charset:{UTF_8:'utf8'},computeHmacSha256Signature:(message,key)=>[...crypto.createHmac('sha256',key).update(message).digest()]},
    LockService:{getScriptLock:()=>({waitLock(){},hasLock:()=>true,releaseLock(){}})},
    CacheService:{getScriptCache:()=>({get:k=>cache.get(k),put:(k,v)=>cache.set(k,v)})},
    ContentService:{MimeType:{JSON:'json'},createTextOutput:text=>({text,setMimeType(){return this;}})},
    SpreadsheetApp:{openById:id=>({getSheetByName:name=>sheets.get(name)}),flush(){}},
    FormApp:{ItemType:{TEXT:'TEXT',MULTIPLE_CHOICE:'MULTIPLE_CHOICE'},openById:id=>id===config.resetForm?resetForm:form}
  };
  vm.createContext(c); vm.runInContext(source,c);
  const config=vm.runInContext('OTT',c);
  const questions=config.questions.map(title=>({getTitle:()=>title,getType:()=> 'TEXT',asTextItem:()=>({isRequired:()=>true})}));
  const form={collectsEmail:()=>true,getDestinationId:()=>null,getItems:()=>questions,getId:()=>config.form,
    getResponses:()=>[...responses.values()],deleteResponse:id=>{deleted.push(id);responses.delete(id);}};
  const resetResponses=new Map(),resetDeleted=[];
  const resetQuestions=config.legacyResetQuestions.map(title=>({getTitle:()=>title,getType:()=> 'TEXT',asTextItem:()=>({isRequired:()=>true})}));
  const accountQuestions=config.resetQuestions.map((title,i)=>({getTitle:()=>title,
    getType:()=>i===1?'MULTIPLE_CHOICE':'TEXT',asTextItem:()=>({isRequired:()=>i===0}),
    asMultipleChoiceItem:()=>({isRequired:()=>true,getChoices:()=>config.resetChoices.map(value=>({getValue:()=>value}))})}));
  const resetForm={collectsEmail:()=>true,getDestinationId:()=>null,getItems:()=>resetQuestions,getId:()=>config.resetForm,
    getResponses:()=>[...resetResponses.values()],deleteResponse:id=>{resetDeleted.push(id);resetResponses.delete(id);}};
  function sheet(header){
    const rows=[[...header]];
    return {rows,getLastRow:()=>rows.length,getMaxRows:()=>1000,insertRowsAfter(){},
      appendRow:row=>rows.push([...row]),getRange:(r,col,count,width)=>{
        if(typeof r==='string')return {setValues(){}};
        return {getValues:()=>Array.from({length:count},(_,i)=>Array.from({length:width},(_,j)=>rows[r+i-1]?.[col+j-1]??'')),
          setValues(values){values.forEach((row,i)=>{rows[r+i-1]??=[];row.forEach((v,j)=>rows[r+i-1][col+j-1]=v);});}};
      }};
  }
  sheets.set('DangKy',sheet(config.header));sheets.set('XepHang',sheet(config.rankHeader));
  sheets.set('MatKhau',sheet(config.resetHeader));
  function addReset(id='reset1'){
    const answers=['230001','new-private-password'];
    resetResponses.set(id,{getId:()=>id,getTimestamp:()=>new Date(),getRespondentEmail:()=> 's@vnu.edu.vn',
      getItemResponses:()=>answers.map((value,i)=>({getItem:()=>resetQuestions[i],getResponse:()=>value}))});
  }
  function useAccountForm(){resetForm.getItems=()=>accountQuestions;}
  function addChange(id,change,password='',repository='',omitBlank=false){
    useAccountForm();
    const answers=['230001',config.resetChoices[{password:0,repository:1,both:2}[change]],password,repository];
    resetResponses.set(id,{getId:()=>id,getTimestamp:()=>new Date(),getRespondentEmail:()=> 's@vnu.edu.vn',
      getItemResponses:()=>answers.map((value,i)=>({getItem:()=>accountQuestions[i],getResponse:()=>value}))
        .filter((value,i)=>!omitBlank || answers[i]!=='')});
  }
  function add(id='response1'){
    const answers=['230001','=IMPORTXML("evil")','https://github.com/student/bot','private-password-123'];
    responses.set(id,{getId:()=>id,getTimestamp:()=>new Date(),getRespondentEmail:()=> 's@vnu.edu.vn',
      getItemResponses:()=>answers.map((value,i)=>({getItem:()=>questions[i],getResponse:()=>value}))});
  }
  function request(data,options={}){
    const envelope={timestamp:Math.floor(Date.now()/1000),nonce:crypto.randomBytes(16).toString('hex'),data:JSON.stringify(data),...options};
    envelope.signature=crypto.createHmac('sha256',props.get('OTTV2_SECRET')).update(`${envelope.timestamp}\n${envelope.nonce}\n${envelope.data}`).digest('hex');
    return {postData:{contents:JSON.stringify(envelope)}};
  }
  const call=data=>JSON.parse(c.doPost(request(data)).text);
  return {c,props,sheets,responses,deleted,form,add,request,call,resetForm,resetResponses,resetDeleted,addReset,
    useAccountForm,addChange,accountQuestions};
}

test('public GET reveals no registration or secret',()=>{
  const f=fixture();f.add();const value=f.c.doGet().text;
  assert(!value.includes('private-password'));assert(!value.includes('s@vnu'));assert(!value.includes('a'.repeat(64)));
});
test('wrong signature, stale timestamp and repeated request never read Forms',()=>{
  const f=fixture();f.form.getResponses=()=>{throw Error('must not access data');};
  const req=f.request({action:'health'});const data=JSON.parse(req.postData.contents);data.signature='0'.repeat(64);
  assert.equal(JSON.parse(f.c.doPost({postData:{contents:JSON.stringify(data)}}).text).ok,false);
  assert.equal(JSON.parse(f.c.doPost(f.request({action:'health'},{timestamp:1})).text).ok,false);
  const valid=f.request({action:'health'});
  assert.equal(JSON.parse(f.c.doPost(valid).text).ok,true);
  assert.equal(JSON.parse(f.c.doPost(valid).text).ok,false);
});
test('pull queues sanitized metadata without password and retries same response',()=>{
  const f=fixture();f.add();
  assert.equal(f.call({action:'pull'}).registrations[0].password,'private-password-123');
  f.call({action:'pull'});
  const rows=f.sheets.get('DangKy').rows;
  assert.equal(rows.length,2);assert.equal(rows[1].length,8);
  assert.equal(rows[1][4],'\'=IMPORTXML("evil")');
  assert(!JSON.stringify(rows).includes('private-password'));
});
test('ack deletes only named Form response, persists metadata and is repeatable',()=>{
  const f=fixture();f.add();f.add('other');f.call({action:'pull'});
  const receipt={response_id:'response1',status:'accepted',message:'Đã tạo tài khoản.'};
  assert.deepEqual(f.call({action:'ack',receipts:[receipt]}).acknowledged,['response1']);
  assert(f.responses.has('other'));assert(!f.responses.has('response1'));
  assert.deepEqual(f.call({action:'ack',receipts:[receipt]}).acknowledged,['response1']);
  assert.equal(f.deleted.length,1);assert.equal(f.sheets.get('DangKy').rows[1][6],'accepted');
});
test('Google failure during deletion leaves queue pending for retry',()=>{
  const f=fixture();f.add();f.call({action:'pull'});f.form.deleteResponse=()=>{throw Error('Google temporary error');};
  assert.equal(f.call({action:'ack',receipts:[{response_id:'response1',status:'accepted',message:'OK'}]}).ok,false);
  assert.equal(f.sheets.get('DangKy').rows[1][6],'pending');assert(f.responses.has('response1'));
});
test('refuse raw password sheet and missing email verification attestation',()=>{
  const f=fixture();f.form.getDestinationId=()=> 'linked-raw-sheet';
  assert.equal(f.call({action:'pull'}).ok,false);
  f.form.getDestinationId=()=>null;f.props.delete('OTTV2_VERIFIED_EMAIL');
  assert.equal(f.call({action:'pull'}).ok,false);
});
test('accept observed Google no-destination exception but fail closed on other errors',()=>{
  const f=fixture();
  for(const message of ['Biểu mẫu hiện không có đích trả lời.',
    'The form currently has no response destination.']){
    for(const form of [f.form,f.resetForm]){
      form.getDestinationId=()=>{throw Error(message);};
    }
    assert.equal(f.call({action:'health'}).ok,true);
  }
  for(const message of ['permission denied','Google temporary error',
    'The form currently has no response destination. permission denied']){
    f.form.getDestinationId=()=>{throw Error(message);};
    assert.equal(f.call({action:'health'}).ok,false);
  }
});
test('rank export escapes formulas, clears stale rows, refuses older overwrite',()=>{
  const f=fixture();const stamp=Date.now()/1000;
  const row=[1,'230001','=FORMULA()',1000,0,0,0,0,'2026-10-24T00:00:00+07:00'];
  assert.equal(f.call({action:'ranking',rows:[row],generated_at:stamp}).ok,true);
  assert.equal(f.sheets.get('XepHang').rows[1][2],"'=FORMULA()");
  assert.equal(f.call({action:'ranking',rows:[],generated_at:stamp-1}).ok,false);
  assert.equal(f.call({action:'ranking',rows:[],generated_at:stamp+1}).ok,true);
  assert(f.sheets.get('XepHang').rows[1].every(value=>value===''));
});

test('reset pulls only reset Form; metadata never contains new password',()=>{
  const f=fixture(); f.add();f.addReset();
  assert.equal(f.call({action:'pull_reset'}).resets[0].password,'new-private-password');
  f.call({action:'pull_reset'});
  const rows=f.sheets.get('MatKhau').rows;
  assert.equal(rows.length,2);assert.equal(rows[1].length,6);
  assert(!JSON.stringify(rows).includes('private-password'));
  assert.equal(f.sheets.get('DangKy').rows.length,1);
  const receipt={response_id:'reset1',status:'accepted',message:'Done'};
  assert.deepEqual(f.call({action:'ack_reset',receipts:[receipt]}).acknowledged,['reset1']);
  assert.equal(f.sheets.get('MatKhau').rows[1][4],'accepted');
  assert(f.responses.has('response1'));assert(!f.resetResponses.has('reset1'));
  assert.deepEqual(f.call({action:'ack_reset',receipts:[receipt]}).acknowledged,['reset1']);
  assert.equal(f.resetDeleted.length,1);
});
test('reset requires independent Verified attestation and no raw response sheet',()=>{
  const f=fixture();f.props.delete('OTTV2_RESET_VERIFIED_EMAIL');
  assert.equal(f.call({action:'pull_reset'}).ok,false);
  assert.equal(f.call({action:'pull'}).ok,true);
  f.props.set('OTTV2_RESET_VERIFIED_EMAIL','reviewed-verified');f.resetForm.getDestinationId=()=> 'raw';
  assert.equal(f.call({action:'pull_reset'}).ok,false);
});

test('combined form accepts password repository and both with optional fields',()=>{
  const f=fixture();
  f.addChange('password','password','new-private-password');
  f.addChange('repository','repository','','https://github.com/new/bot',true);
  f.addChange('both','both','other-private-password','https://github.com/other/bot');
  const result=f.call({action:'pull_reset'});
  assert.equal(result.ok,true);
  assert.deepEqual(result.resets.map(r=>r.change),['password','repository','both']);
  assert.equal(result.resets[1].password,'');
  assert.equal(result.resets[1].repository,'https://github.com/new/bot');
  assert(!JSON.stringify(f.sheets.get('MatKhau').rows).includes('private-password'));
  assert(!JSON.stringify(f.sheets.get('MatKhau').rows).includes('https://github.com'));
});

test('old responses still work after upgrading form items without deleting pending responses',()=>{
  const f=fixture();f.addReset('old-request');f.useAccountForm();
  const result=f.call({action:'pull_reset'});
  assert.equal(result.ok,true);assert.equal(result.resets[0].change,'password');
  assert.equal(result.resets[0].password,'new-private-password');
  assert.equal(f.resetDeleted.length,0);
  assert.equal(f.call({action:'health'}).account_change_form,true);
});

test('combined form refuses altered choices or incorrectly required optional fields',()=>{
  const f=fixture();f.useAccountForm();
  f.accountQuestions[1].asMultipleChoiceItem=()=>({isRequired:()=>true,getChoices:()=>[{getValue:()=> 'changed'}]});
  assert.equal(f.call({action:'pull_reset'}).ok,false);
  const second=fixture();second.useAccountForm();
  second.accountQuestions[2].asTextItem=()=>({isRequired:()=>true});
  assert.equal(second.call({action:'pull_reset'}).ok,false);
});

test('owner migration preserves form ID old items responses and Verified email setting and is repeatable',()=>{
  const f=fixture(),config=vm.runInContext('OTT',f.c),items=[];
  const makeItem=(title,type)=>{
    let required=true,choices=[];
    const item={getTitle:()=>title,getType:()=>type,getIndex:()=>items.indexOf(item),
      setTitle(value){title=value;return this;},setRequired(value){required=value;return this;},
      isRequired:()=>required,asTextItem(){return this;},asMultipleChoiceItem(){return this;},
      setHelpText(){return this;},setValidation(){return this;},
      setChoiceValues(values){choices=[...values];return this;},
      getChoices:()=>choices.map(value=>({getValue:()=>value}))};
    items.push(item);return item;
  };
  const student=makeItem(config.legacyResetQuestions[0],'TEXT');
  const password=makeItem(config.legacyResetQuestions[1],'TEXT');
  f.addReset('pending-before-upgrade');
  f.resetForm.getItems=()=>items;
  f.resetForm.addTextItem=()=>makeItem('','TEXT');
  f.resetForm.addMultipleChoiceItem=()=>makeItem('','MULTIPLE_CHOICE');
  f.resetForm.moveItem=(from,to)=>items.splice(to,0,items.splice(from,1)[0]);
  f.resetForm.setCollectEmail=()=>{throw Error('must preserve Verified mode');};
  for(const method of ['setTitle','setDescription','setConfirmationMessage'])f.resetForm[method]=()=>f.resetForm;
  f.resetForm.getEditUrl=()=> 'https://docs.google.com/forms/d/'+config.resetForm+'/edit';
  f.resetForm.getPublishedUrl=()=> 'https://docs.google.com/forms/d/e/example/viewform';
  f.c.FormApp.createTextValidation=()=>({requireTextMatchesPattern(){return this;},setHelpText(){return this;},build(){return {};}});
  f.c.console={log(){}};
  vm.runInContext(fs.readFileSync('google/setup-password-form.gs','utf8'),f.c);
  const addText=f.resetForm.addTextItem;
  f.resetForm.addTextItem=()=>{throw Error('temporary Google failure');};
  assert.throws(()=>f.c.upgradeAccountChangeForm(),/temporary Google failure/);
  assert.equal(items.length,3);
  f.resetForm.addTextItem=addText;
  const first=f.c.upgradeAccountChangeForm();
  assert.equal(first.form_id,config.resetForm);
  assert.deepEqual(items.map(item=>item.getTitle()),[...config.resetQuestions]);
  assert.equal(items[0],student);assert.equal(items[2],password);
  f.c.upgradeAccountChangeForm();
  assert.equal(items.length,4);assert(f.resetResponses.has('pending-before-upgrade'));
  assert.equal(f.resetDeleted.length,0);assert.equal(f.call({action:'health'}).account_change_form,true);
  assert.equal(f.call({action:'pull_reset'}).resets[0].change,'password');
});
