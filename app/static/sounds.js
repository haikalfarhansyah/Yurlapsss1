/* Passing beeps are immediate. TTS uses a separate FIFO queue. */
(function(){
'use strict';
const api=window.YurLapsUI.api;
const $=id=>document.getElementById(id);
const queue=[];
let speaking=false,audio=null,sessionId=null,lastEvent=0,pendingBest=null,pollBusy=false;
let settings={sound_passing:true,sound_ignore:true,sound_countdown:true,announce_driver:false,
  announce_position_mode:'off',announce_lap:false,announce_best_lap_mode:'off',announce_best_lap_every:3,
  announce_last_lap:false,announce_finish:true,tts_rate:1,tts_voice:'',tts_language:'id-ID',beep_volume:.6};
function audioContext(){
  const AC=window.AudioContext||window.webkitAudioContext;
  if(!AC)return null;
  audio=audio||new AC();
  if(audio.state==='suspended')audio.resume().catch(()=>{});
  return audio;
}
function tone(frequency,duration,type,volume){
  try{
    const ctx=audioContext();if(!ctx)return;
    const oscillator=ctx.createOscillator(),gain=ctx.createGain();
    oscillator.type=type;oscillator.frequency.value=frequency;
    const level=Math.max(.01,Math.min(1,volume)),now=ctx.currentTime;
    gain.gain.setValueAtTime(level,now);
    gain.gain.exponentialRampToValueAtTime(.001,now+duration);
    oscillator.connect(gain);gain.connect(ctx.destination);
    oscillator.start(now);oscillator.stop(now+duration);
    oscillator.onended=()=>{oscillator.disconnect();gain.disconnect();};
  }catch(_){/* Audio failure must not stop timing. */}
}
function countdownTone(value){
  // Short WebAudio tones stay aligned with the one-second countdown even when
  // speech synthesis is busy. The spoken number remains queued separately.
  const ctx=audioContext();if(!ctx)return;
  const now=ctx.currentTime,osc=ctx.createOscillator(),gain=ctx.createGain();
  osc.type='sine';osc.frequency.setValueAtTime(650+Number(value)*28,now);
  gain.gain.setValueAtTime(Math.max(.01,settings.beep_volume*.28),now);
  gain.gain.exponentialRampToValueAtTime(.001,now+.12);
  osc.connect(gain);gain.connect(ctx.destination);osc.start(now);osc.stop(now+.12);
}
function beep(ignore){tone(ignore?520:1450,ignore?.12:.07,ignore?'sawtooth':'square',settings.beep_volume*(ignore?.45:.8));}
function horn(){
  tone(440,.45,'sawtooth',settings.beep_volume*.35);
  tone(554,.45,'sawtooth',settings.beep_volume*.25);
  tone(660,.45,'sawtooth',settings.beep_volume*.2);
}
function drain(){
  if(speaking||!queue.length)return;
  const text=queue.shift();
  if(!window.speechSynthesis||!window.SpeechSynthesisUtterance){setTimeout(drain,0);return;}
  speaking=true;
  const utterance=new SpeechSynthesisUtterance(text);
  utterance.lang=settings.tts_language;utterance.rate=settings.tts_rate;
  const voice=speechSynthesis.getVoices().find(v=>v.name===settings.tts_voice);
  if(voice)utterance.voice=voice;
  utterance.onend=utterance.onerror=()=>{speaking=false;drain();};
  try{speechSynthesis.speak(utterance);}catch(_){speaking=false;drain();}
}
function say(text){if(text){queue.push(String(text));drain();}}
function enqueue(job){
  if(job?.type==='beep')beep(!!job.ignore);
  else if(job?.type==='horn')horn();
  else if(job?.type==='tts')say(job.text);
}
function ttsName(e){return String(e.tts_name||'').trim();}
function bestAnnouncement(e){if(ttsName(e))say(`${ttsName(e)} best lap ${(Number(e.lap_ms)/1000).toFixed(3)}`);}
function processEvents(race){
  const sid=race.session_id;
  if(sid!==sessionId){sessionId=sid;pendingBest=null;}
  // IDs are monotonic for the engine lifetime, even across Reset. Never reset
  // this cursor while iterating old events or announcements would replay.
  for(const e of race.events||[]){
    if(!Number.isFinite(e.id)||e.id<=lastEvent)continue;
    lastEvent=e.id;
    const name=ttsName(e),pos=settings.announce_position_mode,best=settings.announce_best_lap_mode;
    if(e.kind==='countdown'&&settings.sound_countdown){countdownTone(e.value);const words=['nol','satu','dua','tiga','empat','lima','enam','tujuh','delapan','sembilan','sepuluh'];say(settings.tts_language.startsWith('id')?(words[e.value]||String(e.value)):String(e.value));}
    else if(e.kind==='go'&&settings.sound_countdown)horn();
    else if(e.kind==='stagger_call'&&settings.announce_driver&&name)say(name);
    else if(e.kind==='driver_passing'&&!e.finished&&!e.last_lap&&name){
      const parts=[];
      // `changed` is announced by the separate position_change event, so a
      // single passing cannot produce the same position announcement twice.
      if(e.position&&pos==='passing')parts.push('posisi '+e.position);
      if(settings.announce_lap&&!e.origin)parts.push('lap '+e.lap);
      if(parts.length)say(name+' '+parts.join(' '));
    }
    else if(e.kind==='best_lap'){
      if(best==='immediate')bestAnnouncement(e);
      else if(best==='interval')pendingBest=e;
    }else if(e.kind==='lap'){
      if(pendingBest&&best==='interval'&&e.global_lap>0&&e.global_lap%settings.announce_best_lap_every===0){bestAnnouncement(pendingBest);pendingBest=null;}
    }else if(e.kind==='last_lap'&&settings.announce_last_lap&&name){
      say(`${name}${pos!=='off'&&e.position?' posisi '+e.position:''} last lap`);
    }else if(e.kind==='driver_finish'&&settings.announce_finish&&name){
      say(`${name} finish${e.position?' posisi '+e.position:''}`);
    }else if(e.kind==='finish'&&settings.announce_finish){
      say('Race selesai');
      for(const row of e.results||[])if(ttsName(row)&&row.position)say(`${ttsName(row)} posisi ${row.position}`);
    }
  }
}
function formState(){
  $('soundBestEveryField').hidden=$('soundBestMode').value!=='interval';
}
function refreshVoices(){
  const select=$('soundVoice');if(!select)return;
  select.replaceChildren();
  const add=(value,label)=>{const option=document.createElement('option');option.value=value;option.textContent=label;select.appendChild(option);};
  add('','Suara perangkat (default)');
  if(window.speechSynthesis)speechSynthesis.getVoices().forEach(v=>add(v.name,v.name+' · '+v.lang));
  select.value=settings.tts_voice;
}
function renderSettings(){
  const checks={soundCountdown:'sound_countdown',soundDriver:'announce_driver',soundLap:'announce_lap',soundLastLap:'announce_last_lap',soundFinish:'announce_finish'};
  Object.entries(checks).forEach(([id,key])=>$(id).checked=!!settings[key]);
  $('soundPositionMode').value=settings.announce_position_mode;
  $('soundBestMode').value=settings.announce_best_lap_mode;
  $('soundBestEvery').value=settings.announce_best_lap_every;
  $('soundRate').value=settings.tts_rate;$('soundLanguage').value=settings.tts_language;$('soundVolume').value=settings.beep_volume;
  refreshVoices();formState();
}
async function load(){
  try{
    const r=await api('/api/sound/settings'),saved=r.settings||{};
    settings=Object.assign(settings,saved);
    if(!saved.announce_position_mode)settings.announce_position_mode=saved.announce_position_change?'changed':'off';
    if(!saved.announce_best_lap_mode)settings.announce_best_lap_mode=saved.announce_best_lap?'interval':'off';
    renderSettings();$('soundSaved').textContent='tersimpan';
  }catch(e){$('soundSaved').textContent='Gagal memuat pengaturan';}
}
let saveChain=Promise.resolve();
function save(){
  formState();
  const n=Number($('soundBestEvery').value),rate=Number($('soundRate').value),volume=Number($('soundVolume').value);
  if(!Number.isInteger(n)||n<1||n>999||!Number.isFinite(rate)||rate<.5||rate>2){$('soundSaved').textContent='Periksa interval dan kecepatan suara';return;}
  const body={sound_passing:true,sound_ignore:true,sound_countdown:$('soundCountdown').checked,
    announce_driver:$('soundDriver').checked,announce_position_mode:$('soundPositionMode').value,
    announce_position_change:$('soundPositionMode').value==='changed',announce_lap:$('soundLap').checked,
    announce_best_lap_mode:$('soundBestMode').value,announce_best_lap:$('soundBestMode').value!=='off',
    announce_best_lap_every:n,announce_last_lap:$('soundLastLap').checked,announce_finish:$('soundFinish').checked,
    tts_rate:rate,tts_voice:$('soundVoice').value,tts_language:$('soundLanguage').value,beep_volume:volume};
  $('soundSaved').textContent='menyimpan…';
  saveChain=saveChain.then(async()=>{try{await api('/api/settings',body);settings={...settings,...body};pendingBest=null;$('soundSaved').textContent='tersimpan';}catch(e){$('soundSaved').textContent='Gagal: '+e.message;}});
}
['soundCountdown','soundDriver','soundPositionMode','soundLap','soundBestMode','soundLastLap','soundFinish','soundBestEvery','soundLanguage','soundRate','soundVoice','soundVolume'].forEach(id=>$(id).addEventListener('change',save));
$('soundTest').addEventListener('click',()=>{beep(false);say('Tes suara YurLaps');});
const unlock=()=>{const c=audioContext();$('soundAudioState').textContent=c?'Audio mengikuti output perangkat aktif. Beep setiap passing, TTS bergantian.':'Audio Web tidak tersedia pada perangkat ini.';};
document.addEventListener('pointerdown',unlock,{once:true});document.addEventListener('keydown',unlock,{once:true});
if(window.speechSynthesis)speechSynthesis.onvoiceschanged=refreshVoices;
const token=new URLSearchParams(window.location.search).get('t');
const source=new EventSource('/api/events'+(token?'?t='+encodeURIComponent(token):''));
source.onmessage=ev=>{try{const e=JSON.parse(ev.data);if(e.kind==='race_passing')beep(!e.result?.diterima);}catch(_) {}};
// One warm-up snapshot avoids replaying already spoken events after reopening
// the window. Subsequent snapshots can be consumed by the main UI as well.
let ready=false;
const poll=async()=>{if(pollBusy)return;pollBusy=true;try{const race=(await api('/api/race/state')).race||{};if(!ready){sessionId=race.session_id;lastEvent=Math.max(0,...(race.events||[]).map(e=>e.id));ready=true;}else processEvents(race);}catch(_){}finally{pollBusy=false;}};
window.YurLapsSound={enqueue,say,load,processEvents};
window.YurLapsEvents={race:race=>{if(ready)processEvents(race);}};
load().then(poll);setInterval(poll,250);
})();
