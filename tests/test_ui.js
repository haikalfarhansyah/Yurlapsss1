'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const root=path.join(__dirname,'../app/static');
function element(){return {value:'',checked:false,hidden:false,disabled:false,textContent:'',innerHTML:'',style:{},dataset:{},classList:{toggle(){},add(){},remove(){}},handlers:{},addEventListener(k,fn){this.handlers[k]=fn;},querySelectorAll(){return [];},replaceChildren(){},appendChild(){}};}
function harness(settings={}){
 const nodes=new Map(),intervals=[],spoken=[],tones=[],requests=[],sources=[];
 const get=id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);};
 class AC{constructor(){this.state='running';this.currentTime=0;this.destination={};}createOscillator(){const o={frequency:{value:0},connect(){},disconnect(){},start(){tones.push(o);},stop(){}};return o;}createGain(){return {gain:{setValueAtTime(){},exponentialRampToValueAtTime(){}},connect(){},disconnect(){}};}}
 class ES{constructor(){sources.push(this);}}
 const context={console,URLSearchParams,Map,Set,Date,Promise,Number,Math,Error,SpeechSynthesisUtterance:class{constructor(text){this.text=text;}},EventSource:ES,
  document:{getElementById:get,createElement:element,addEventListener(){},querySelectorAll(){return [];}},setInterval(fn){intervals.push(fn);},setTimeout(fn){return 1;},clearInterval(){},clearTimeout(){}};
 context.window={location:{search:''},AudioContext:AC,SpeechSynthesisUtterance:context.SpeechSynthesisUtterance,speechSynthesis:{getVoices:()=>[],speak:u=>spoken.push(u)},YurLapsUI:{api:async(url,body)=>{requests.push([url,body]);return url==='/api/sound/settings'?{settings}:{race:{session_id:'warmup',events:[]}};}}};
 context.speechSynthesis=context.window.speechSynthesis;
 vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(root,'sounds.js'),'utf8'),context);
 const drain=()=>{let n=0;while(n<spoken.length){const utterance=spoken[n++];if(!utterance._ended){utterance._ended=true;utterance.onend();}}return spoken.map(u=>u.text);};
 return {context,nodes,get,intervals,spoken,tones,requests,sources,drain,sound:context.window.YurLapsSound};
}
const settle=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
test('TTS FIFO does not delay simultaneous passing beeps; raw event never doubles beep',async()=>{
 const h=harness();await settle();h.sound.say('Irul lap 1');h.sound.say('Budi lap 1');assert.equal(h.spoken.length,1);
 const send=e=>h.sources[0].onmessage({data:JSON.stringify(e)});
 send({kind:'race_passing',result:{diterima:true}});send({kind:'race_passing',result:{diterima:false}});send({kind:'passing'});
 assert.equal(h.tones.length,2);assert.equal(h.tones[0].frequency.value,1450);assert.equal(h.tones[1].frequency.value,520);
 assert.deepEqual(h.drain(),['Irul lap 1','Budi lap 1']);
});
test('repeated snapshots do not replay countdown, laps or finish',async()=>{
 const h=harness({announce_lap:true});await settle();
 const race={session_id:'one',events:[{id:1,kind:'countdown',value:5},{id:2,kind:'driver_passing',tts_name:'Irul',lap:1},{id:3,kind:'driver_finish',tts_name:'Irul',position:1}]};
 h.sound.processEvents(race);h.sound.processEvents(race);h.sound.processEvents(race);
 assert.deepEqual(h.drain(),['lima','Irul lap 1','Irul finish posisi 1']);
 h.sound.processEvents({session_id:'two',events:[{id:4,kind:'countdown',value:3}]});assert.equal(h.drain().filter(x=>x==='tiga').length,1);
});
test('new global record is retained until Nth global lap even when that lap is not best',async()=>{
 const h=harness({announce_best_lap_mode:'interval',announce_best_lap_every:3});await settle();
 const events=[{id:1,kind:'best_lap',tts_name:'Irul',lap_ms:10500,global_lap:1},{id:2,kind:'lap',global_lap:1},{id:3,kind:'lap',global_lap:2}];
 h.sound.processEvents({session_id:'one',events});assert.equal(h.spoken.length,0);
 events.push({id:4,kind:'lap',global_lap:3});h.sound.processEvents({session_id:'one',events});assert.deepEqual(h.drain(),['Irul best lap 10.500']);
 events.push({id:5,kind:'lap',global_lap:6});h.sound.processEvents({session_id:'one',events});assert.equal(h.drain().length,1);
});
test('last lap and finish suppress normal lap speech and GO sounds a horn',async()=>{
 const h=harness({announce_lap:true,announce_last_lap:true});await settle();
 h.sound.processEvents({session_id:'one',events:[{id:1,kind:'go'},{id:2,kind:'last_lap',tts_name:'Irul'},{id:3,kind:'lap',tts_name:'Irul',lap:9,last_lap:true},{id:4,kind:'driver_finish',tts_name:'Irul'},{id:5,kind:'lap',tts_name:'Irul',lap:10,finished:true}]});
 assert.equal(h.tones.length,3);assert.deepEqual(h.drain(),['Irul last lap','Irul finish']);
});
test('position mode uses only crossing driver; missing TTS is never fabricated',async()=>{
 const h=harness({announce_position_mode:'passing'});await settle();
 h.sound.processEvents({session_id:'one',events:[{id:1,kind:'driver_passing',tts_name:'Irul',position:2},{id:2,kind:'position_change',tts_name:'Budi',position:3},{id:3,kind:'driver_finish',name:'Account without TTS',position:1}]});
 assert.deepEqual(h.drain(),['Irul posisi 2']);
});
function functionText(source,name){const start=source.indexOf('function '+name+'(');assert.ok(start>=0,name);const body=source.indexOf('{',start);let depth=1,index=body+1;for(;depth&&index<source.length;index++){if(source[index]==='{')depth++;else if(source[index]==='}')depth--;}return source.slice(start,index);}
const quick=fs.readFileSync(path.join(root,'quickrace.js'),'utf8');
function pure(names,extra={}){const context={Math,Number,Map,Set,Date,console,...extra};vm.createContext(context);vm.runInContext(names.map(name=>functionText(quick,name)).join('\n'),context);return context;}
test('practice flattens session drivers and uses immutable names, real top speed, three full laps for top3',()=>{
 const p=pure(['rata','sigma','practiceStats','sessionLapsByTp','practiceRows']);
 const session={drivers:[{transponder:12,name:'Original owner',nickname:'Irul',laps_ms:[10000,11000],passings:[{speed_kmh:26.4}]}]};
 const rows=p.practiceRows(session);assert.equal(rows[0].name,'Irul');assert.equal(rows[0].topSpeed,26.4);assert.equal(rows[0].top3,null);assert.equal(rows[0].laps,2);
 session.drivers[0].laps_ms.push(9000);assert.equal(p.practiceRows(session)[0].top3,10000);
});
test('race controls enforce finish then explicit reset and lock paused settings',()=>{
 const nodes=new Map();const $=id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);};
 const p=pure(['renderTombol'],{$,sesi:{status:'live'},state:{presetId:'p',peserta:[{}]},trackAktif:()=>({id:'t'}),SVG_PLAY:''});
 p.renderTombol();assert.equal($('btnStart').style.display,'none');assert.equal($('btnFinish').style.display,'');assert.equal($('btnReset').style.display,'none');
 p.sesi={status:'paused',start_decoder_ts:0};p.renderTombol();assert.equal($('btnPeserta').disabled,true);assert.equal($('selTrack').disabled,true);
 p.sesi={status:'done',can_reset:false};p.renderTombol();assert.equal($('btnReset').style.display,'none');assert.equal($('btnReview').style.display,'');
 p.sesi={status:'done',can_reset:true,saved_id:'saved'};p.renderTombol();assert.equal($('btnReset').style.display,'');assert.equal($('btnStart').style.display,'none');
});
function appHarness(){
 const html=fs.readFileSync(path.join(root,'quickrace.html'),'utf8'),ids=[...html.matchAll(/id="([^"]+)"/g)].map(m=>m[1]);
 const nodes=new Map(ids.map(id=>[id,{...element(),options:[],setAttribute(){},focus(){},getBoundingClientRect:()=>({top:0}),querySelector:()=>null}]));
 const intervals=[],calls=[],failures=[];let now={status:'live',session_id:'one',cfg:{name:'Final',nama:'Final',target:'laps',value:10,start_mode:'grid',rank:'position',cd:5},track_id:'t',track:{id:'t',name:'Real track',min_lap_ms:1500},peserta:[],klasemen:[],log:[{nama:'Irul',transponder:12,lap:1,status:'lap',rssi:-25,hit_count:6,dur_us:90000,decoder_timecode:1234}],events:[],elapsed_ms:1000};
 const practice={active:true,idle_seconds:120,session:{track_name:'Real track'},active_sessions:[{id:'s',track_name:'Real track',started_at:100,drivers:[{driver_id:'owner',transponder:12,name:'Irul original',nickname:'Irul',laps_ms:[10000,11000,9000],passings:[{seq:1,lap_ms:10000,rssi:-26,hit_count:5,pass_duration_us:90000,speed_kmh:12}]}]}]};
 const clone=x=>JSON.parse(JSON.stringify(x));
 const fetch=async(url,opts={})=>{const route=url.split('?')[0];calls.push(route);let data={ok:true};if(route==='/api/club/bootstrap')Object.assign(data,{club:{name:'Club'},tracks:[now.track],members:[],presets:[{id:'p',name:'Final'}],race:clone(now)});else if(route==='/api/race/state')data.race=clone(now);else if(route==='/api/practice/state')data.practice=clone(practice);else if(route==='/api/practice/history')data.sessions=[];else if(route==='/api/cloud/status')data.cloud={online:true,clubs:[]};else if(route==='/api/races')data.races=[];else if(route==='/api/state')data.decoder={online:true,status:{noise_power:-65},counters:{},passes:[]};else if(route==='/api/race/finish'){now.status='done';data.race=clone(now);}else if(route==='/api/race/discard'){now.discarded=true;now.can_reset=true;data.race=clone(now);}else if(route==='/api/race/save'){now.saved_id='saved';now.can_reset=true;data.tersimpan={nama:'Final'};}else if(route==='/api/race/reset'){now.status='idle';now.can_reset=false;data.race=clone(now);}else if(route==='/api/race/log/clear'){now.log=[];data.race=clone(now);}return {ok:true,status:200,json:async()=>data};};
 const document={getElementById:id=>nodes.get(id)||null,querySelectorAll:()=>[],querySelector:()=>null,addEventListener(){},activeElement:null,body:{classList:{add(){},remove(){}},insertAdjacentHTML:(_where,text)=>failures.push(text)}};
 const context={console,document,fetch,URLSearchParams,Map,Set,Date,Math,Number,Promise,Error,confirm:()=>true,setTimeout:()=>1,clearTimeout(){},setInterval(fn,ms){intervals.push({fn,ms});},clearInterval(){},navigator:{onLine:true}};
 context.window={location:{search:''},scrollTo(){},addEventListener(){},matchMedia:()=>({matches:true})};vm.createContext(context);vm.runInContext(quick,context);
 return {nodes,calls,failures,intervals,getRace:()=>now};
}
test('production UI boots from actual session payload, finish cancel stays live, save waits for explicit Reset, clear log works',async()=>{
 const h=appHarness();for(let i=0;i<12;i++)await settle();assert.deepEqual(h.failures,[]);
 assert.match(h.nodes.get('practiceBody').innerHTML,/Irul/);assert.match(h.nodes.get('practiceBody').innerHTML,/12.00 km\/h/);
 h.nodes.get('btnFinish').onclick();assert.equal(h.nodes.get('modalFinish').hidden,false);
 h.nodes.get('finishCancel').onclick();assert.equal(h.getRace().status,'live');assert.ok(!h.calls.includes('/api/race/finish'));
 await h.nodes.get('finishSave').onclick();assert.equal(h.getRace().status,'done');assert.equal(h.nodes.get('modalSave').hidden,false);
 await h.nodes.get('saveOk').onclick();assert.equal(h.getRace().status,'done');assert.equal(h.nodes.get('btnReset').style.display,'');assert.ok(!h.calls.includes('/api/race/reset'));
 await h.nodes.get('btnClearLog').onclick();assert.ok(h.calls.includes('/api/race/log/clear'));assert.match(h.nodes.get('pLog').innerHTML,/Belum ada passing/);
 await h.nodes.get('btnReset').onclick();assert.equal(h.getRace().status,'idle');assert.ok(h.calls.includes('/api/race/reset'));
});
