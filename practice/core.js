'use strict';
// Pure rules shared by the downloadable practice page and its regression tests.
const PracticeCore=(()=>{
  const sides=['A','B'],kinds=['rock','paper','scissors'];
  const beats={rock:'scissors',scissors:'paper',paper:'rock'};
  const exact=(o,keys)=>o&&typeof o==='object'&&!Array.isArray(o)&&Object.keys(o).sort().join(',')===keys.slice().sort().join(',');
  function coord(p){if(typeof p!=='string'||!(/^[a-i][1-9]$/).test(p))throw Error('Tọa độ phải nằm trong a1–i9.');return [p.charCodeAt(0)-97,+p[1]-1];}
  function validateConfig(c){
    if(!c||c.type!=='ottv2_practice_config'||c.schema_version!==1)throw Error('Hãy dùng cấu hình từ nút Sao chép cấu hình đấu thử trên website.');
    const m=c.map;
    if(!exact(m,['schema_version','name','board','goals','bombs','obstacles'])||m.schema_version!==1||typeof m.name!=='string'||!m.name.trim()||m.name.length>80)throw Error('Bản đồ không đúng định dạng.');
    if(!exact(m.goals,sides)||m.goals.A===m.goals.B||!m.board||typeof m.board!=='object'||Array.isArray(m.board)||Object.keys(m.board).length!==18)throw Error('Bản đồ cần 18 quân và hai đích khác nhau.');
    const count={A:{rock:0,paper:0,scissors:0},B:{rock:0,paper:0,scissors:0}};
    for(const [p,v]of Object.entries(m.board)){coord(p);if(!exact(v,['side','kind'])||!sides.includes(v.side)||!kinds.includes(v.kind))throw Error('Quân cờ không hợp lệ.');count[v.side][v.kind]++;}
    for(const s of sides){coord(m.goals[s]);if(kinds.some(k=>count[s][k]!==3)||m.board[m.goals[s]]?.side===s)throw Error('Mỗi bên cần 3 quân mỗi loại và không đứng sẵn trên đích của mình.');}
    for(const key of ['bombs','obstacles']){if(!Array.isArray(m[key])||new Set(m[key]).size!==m[key].length)throw Error('Bom/vật cản bị trùng.');m[key].forEach(coord);}
    const goals=Object.values(m.goals);
    if(m.bombs.some(p=>m.board[p]||goals.includes(p))||m.obstacles.some(p=>m.board[p]||goals.includes(p)||m.bombs.includes(p)))throw Error('Các vật trên bản đồ bị chồng lên nhau.');
    if(!exact(c.bots,['beginner','intermediate','advanced'])||Object.values(c.bots).some(s=>typeof s!=='string'||!s.trim()||new TextEncoder().encode(s).length>65536))throw Error('Cần đủ ba mã Python, mỗi mã tối đa 64 KiB.');
    const l=c.limits;
    if(!exact(l,['cpu_budget_seconds','response_limit_seconds','memory_limit_mib_per_bot','max_valid_moves','move_limit_tiebreak'])||l.cpu_budget_seconds!==7||l.response_limit_seconds!==2.5||l.memory_limit_mib_per_bot!==64||l.max_valid_moves!==50||l.move_limit_tiebreak!=='piece_count')throw Error('Cấu hình phải dùng luật hiện hành: 7 giây, 2,5 giây/lượt, 25 nước mỗi bên.');
    return JSON.parse(JSON.stringify(c));
  }
  class Game{
    constructor(map,first='A',limit=50){this.board=JSON.parse(JSON.stringify(map.board));this.goals={...map.goals};this.bombs=new Set(map.bombs);this.obstacles=new Set(map.obstacles);this.turn=first;this.limit=limit;this.history=[];this.result=null;this.reason=null;this.seen=new Map([[this.key(),1]]);}
    key(){return JSON.stringify([Object.keys(this.board).sort().map(p=>[p,this.board[p].side,this.board[p].kind]),[...this.bombs].sort(),this.turn]);}
    validate(move){
      if(!exact(move,['from','to']))throw Object.assign(Error('Nước đi cần đúng hai trường from và to.'),{format:true});
      let a,b;try{a=coord(move.from);b=coord(move.to);}catch(e){e.format=true;throw e;}
      const p=this.board[move.from],t=this.board[move.to];
      if(this.result||!p||p.side!==this.turn||Math.max(Math.abs(a[0]-b[0]),Math.abs(a[1]-b[1]))!==1||this.obstacles.has(move.to)||t&&(t.side===p.side||beats[p.kind]!==t.kind))throw Error('Nước đi không hợp lệ; bot được thử lại trong thời gian còn lại của lượt.');
    }
    state(remaining){return {protocol_version:1,type:'state',you:this.turn,turn:this.turn,board:JSON.parse(JSON.stringify(this.board)),goals:{...this.goals},bombs:[...this.bombs].sort(),obstacles:[...this.obstacles].sort(),time:{cpu_budget_seconds:7,cpu_remaining_seconds:{...remaining},response_limit_seconds:2.5,response_remaining_seconds:2.5}};}
    play(move){
      this.validate(move);const side=this.turn,other=side==='A'?'B':'A',p=this.board[move.from],captured=this.board[move.to];delete this.board[move.from];delete this.board[move.to];const bomb=this.bombs.delete(move.to);
      if(!bomb)this.board[move.to]=p;
      if(!bomb&&move.to===this.goals[side]){this.result=side;this.reason='goal';}
      else for(const loser of [side,other]){if(new Set(Object.values(this.board).filter(p=>p.side===loser).map(p=>p.kind)).size!==3){this.result=loser===side?other:side;this.reason='missing_kind';break;}}
      this.turn=other;const key=this.key(),n=(this.seen.get(key)||0)+1;this.seen.set(key,n);
      if(!this.result&&this.history.length+1>=this.limit){const a=Object.values(this.board).filter(p=>p.side==='A').length,b=Object.values(this.board).length-a;this.result=a===b?'draw':a>b?'A':'B';this.reason=a===b?'move_limit':'material_advantage';}
      if(!this.result&&n>=3){this.result='draw';this.reason='threefold_repetition';}
      const event={side,...move,capture:captured?.kind||null,bomb,result:this.result,reason:this.reason};this.history.push(event);return event;
    }
  }
  return {validateConfig,Game};
})();
if(typeof module!=='undefined')module.exports=PracticeCore;
