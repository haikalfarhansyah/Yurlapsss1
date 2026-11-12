(function(){
'use strict';
const $=id=>document.getElementById(id);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const token=new URLSearchParams(location.search).get('t');
let events=[], selected=null, results=[], standings=[], members=[], tracks=[], presets=[], race=null, sync=null, tab='active';
let timer=null;

async function api(path,body){
  if(token)path+=(path.includes('?')?'&':'?')+'t='+encodeURIComponent(token);
  const options=body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};
  const response=await fetch(path,options);
  let data={};try{data=await response.json();}catch(_e){}
  if(!response.ok||data.ok===false)throw Error(data.error||'Permintaan event gagal');
  return data;
}
function fail(error){
  const message=error?.message||String(error);
  const area=$('eventLive');
  area.innerHTML='<p role="alert" class="note" style="color:var(--bad)">'+esc(message)+'</p>';
}
function className(id){return selected?.classes.find(c=>c.id===id)?.name||'—';}
function fmt(ms){
  if(ms==null||!Number.isFinite(Number(ms)))return '—';
  const value=Math.round(Number(ms)),minutes=Math.floor(value/60000),seconds=Math.floor(value/1000)%60;
  return (minutes?minutes+':':'')+String(seconds).padStart(2,'0')+'.'+String(value%1000).padStart(3,'0');
}
async function load(preferred){
  const [list,club,people,settings]=await Promise.all([
    api('/api/event/list'),api('/api/tracks'),api('/api/members'),api('/api/presets')]);
  events=list.events||[];tracks=club.tracks||[];members=people.members||[];presets=settings.presets||[];
  window.YurLapsEventTimingLock?.(events.some(e=>e.stages.some(s=>s.status==='live')));
  const target=preferred||selected?.id||events.find(e=>e.status==='running')?.id||events.find(e=>e.status!=='completed')?.id||events[0]?.id;
  if(target&&events.some(e=>e.id===target)){
    const detail=await api('/api/event/detail?id='+encodeURIComponent(target));
    selected=detail.event;results=detail.results||[];standings=detail.standings||[];race=detail.race;sync=detail.sync;
  }else{selected=null;results=[];standings=[];race=null;sync=null;}
  render();
}
function renderTabs(){
  document.querySelectorAll('[data-event-tab]').forEach(button=>{
    button.classList.toggle('on',button.dataset.eventTab===tab);
    button.setAttribute('aria-selected',button.dataset.eventTab===tab?'true':'false');
  });
  for(const key of ['active','create','history'])$('event-'+key).hidden=key!==tab;
}
function renderList(){
  const active=events.filter(e=>e.status!=='completed');
  const history=events.filter(e=>e.status==='completed');
  $('eventActiveList').innerHTML=active.length?active.map(e=>`<button class="event-item ${selected?.id===e.id?'on':''}" data-event-select="${esc(e.id)}"><b>${esc(e.name)}</b><small>${esc(e.track_name)} · ${esc(e.date||'Tanggal belum diisi')} · ${esc(e.status)}</small></button>`).join(''):'<div class="empty"><b>Belum ada event aktif</b>Buat event untuk menyusun kelas dan sesi.</div>';
  $('eventHistoryList').innerHTML=history.length?history.map(e=>`<button class="event-item" data-event-select="${esc(e.id)}"><b>${esc(e.name)}</b><small>${esc(e.track_name)} · ${esc(e.date||'—')} · ${e.stages.length} sesi</small></button>`).join(''):'<div class="empty"><b>Belum ada event selesai</b>Hasil resmi event akan muncul di sini.</div>';
}
function renderCreate(){
  $('eventTrack').innerHTML=tracks.map(t=>`<option value="${esc(t.id)}">${esc(t.name)}</option>`).join('');
  if(!$('eventDate').value)$('eventDate').value=new Date().toISOString().slice(0,10);
}
function renderEntries(){
  const classes=selected?.classes||[];
  const options=classes.map(c=>`<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('');
  const current=$('eventEntryClass').value;
  $('eventEntryClass').innerHTML=options;$('eventEntryClass').value=classes.some(c=>c.id===current)?current:classes[0]?.id||'';
  const currentMember=$('eventEntryMember').value;
  $('eventEntryMember').innerHTML=members.map(m=>`<option value="${esc(m.id)}">${esc(m.nick||m.name)} · ${esc(m.source==='local'?'Tamu lokal':'Member/Tamu')}</option>`).join('');
  if(members.some(m=>m.id===currentMember))$('eventEntryMember').value=currentMember;
  renderCars();
  const entries=selected?.entries||[];
  $('eventEntries').innerHTML=entries.length?entries.map(e=>`<div class="event-row"><div><b>${esc(e.nick||e.name)}</b><div class="event-muted">${esc(className(e.class_id))} · ${esc(e.transponder)} · ${esc(e.car_brand||'Mobil belum diisi')}</div><small class="event-status">${e.approved?(e.checked_in?'Hadir':'Disetujui · belum check-in'):'Menunggu persetujuan'}</small></div><div class="actions">${!members.some(m=>m.id===e.member_id)&&members.some(m=>(m.transponder||[]).some(tp=>String(tp)===String(e.transponder)))?`<button class="btn sm" data-entry-action="reconcile" data-id="${esc(e.id)}">Cocokkan akun</button>`:''}${!e.approved?`<button class="btn sm" data-entry-action="approve" data-id="${esc(e.id)}">Setujui</button>`:`<button class="btn sm" data-entry-action="${e.checked_in?'checkout':'checkin'}" data-id="${esc(e.id)}">${e.checked_in?'Batalkan hadir':'Check-in'}</button>`}${selected.status==='draft'||selected.status==='registration'?`<button class="btn sm danger" data-entry-action="remove" data-id="${esc(e.id)}">Hapus</button>`:''}</div></div>`).join(''):'<p class="note">Belum ada peserta. Tambahkan member atau tamu dan pilih mobilnya.</p>';
  $('eventEntryForm').hidden=!selected||!['draft','registration'].includes(selected.status);
}
function renderCars(){
  const member=members.find(m=>m.id===$('eventEntryMember').value);
  const details=member?.transponder_details||[];
  $('eventEntryCar').innerHTML=(member?.transponder||[]).map(tp=>{
    const d=details.find(item=>String(item.transponder_id)===String(tp))||{};
    return `<option value="${esc(tp)}">${esc(tp)} · ${esc(d.rc_class||member.kelas||'Kelas belum diisi')} · ${esc(d.rc_brand||member.mobil||'Mobil belum diisi')}</option>`;
  }).join('');
}
function renderStages(){
  const classes=selected?.classes||[];
  const selectedClass=$('eventStageClass').value;
  $('eventStageClass').innerHTML=classes.map(c=>`<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('');
  if(classes.some(c=>c.id===selectedClass))$('eventStageClass').value=selectedClass;
  $('eventStagePreset').innerHTML='<option value="">Atur langsung di bawah</option>'+presets.map(p=>`<option value="${esc(p.id)}">${esc(p.name)} · ${p.target==='laps'?esc(p.value)+' lap':esc(p.value)+' detik'}</option>`).join('');
  renderStageEntries();
  const stages=selected?.stages||[];
  $('eventStages').innerHTML=stages.length?stages.map(s=>`<div class="event-row"><div><b>${esc(s.sequence)}. ${esc(s.name)}</b><div class="event-muted">${esc(className(s.class_id))} · ${esc(s.group)} · ${esc(s.kind)} · ${s.entry_ids.length?s.entry_ids.length+' peserta':'semua peserta kelas'}</div><small class="event-status">${s.status==='saved'?'Hasil tersimpan':s.status==='live'?(race?.status==='idle'?'Timing terputus':'Sedang berjalan'):'Terjadwal'}</small></div><div class="actions">${selected.status==='running'&&s.status==='planned'?`<button class="btn sm primary" data-stage-action="start" data-id="${esc(s.id)}">Jalankan</button>`:''}${s.status==='planned'?`<button class="btn sm danger" data-stage-action="remove" data-id="${esc(s.id)}">Hapus</button>`:''}${s.status==='live'&&race?.status!=='idle'?`<button class="btn sm fin" data-stage-action="save" data-id="${esc(s.id)}">Selesai & simpan</button>`:''}${s.status==='live'&&race?.status==='idle'?`<button class="btn sm" data-stage-action="retry" data-id="${esc(s.id)}">Jalankan ulang</button>`:''}${s.status==='saved'&&race?.status==='done'&&race?.saved_id===s.race_id?`<button class="btn sm" data-stage-action="reset" data-id="${esc(s.id)}">Siap sesi berikutnya</button>`:''}</div></div>`).join(''):'<p class="note">Belum ada sesi. Tambahkan kualifikasi, heat, main, atau final sesuai format event.</p>';
  $('eventStageForm').hidden=!selected||selected.status==='completed';
}
function renderStageEntries(){
  const classId=$('eventStageClass').value;
  const entries=(selected?.entries||[]).filter(e=>e.class_id===classId);
  $('eventStageEntries').innerHTML=entries.map(e=>`<label style="display:inline-flex;align-items:center;gap:4px;margin:6px 12px 0 0"><input type="checkbox" value="${esc(e.id)}" style="width:auto;margin:0">${esc(e.nick||e.name)}</label>`).join('')||'<span class="event-muted">Belum ada peserta di kelas ini.</span>';
}
function renderResults(){
  const saved=results.filter(r=>r.status==='saved');
  const overall=standings.map(group=>`<div style="margin:10px 0"><b>${esc(className(group.class_id))} · Main ${esc(group.group)}</b><div class="event-board"><table><thead><tr><th>Rank</th><th>Driver</th><th class="r">Poin</th><th class="r">Leg dihitung</th></tr></thead><tbody>${group.drivers.map(d=>`<tr><td>${d.position}</td><td>${esc(d.nick||d.name)}</td><td class="r">${d.points}</td><td class="r">${d.scores.join(' + ')}</td></tr>`).join('')}</tbody></table></div></div>`).join('');
  const content=(overall?`<div class="event-muted">Klasemen main/final · ${selected.best_legs} leg terbaik dihitung</div>${overall}`:'<p class="note">Klasemen akhir muncul setelah main/final disimpan.</p>')+
    (saved.length?'<h3 style="margin-top:18px">Hasil per sesi</h3>'+saved.map(r=>`<details style="margin:8px 0"><summary style="cursor:pointer;font-weight:600">${esc(className(r.class_id))} · ${esc(r.name)} · ${esc(r.group)}</summary><div class="event-board"><table><thead><tr><th>Pos</th><th>Driver</th><th>Status</th><th class="r">Lap</th><th class="r">Total</th><th class="r">Best lap</th></tr></thead><tbody>${(r.drivers||[]).map(d=>`<tr><td>${esc(d.pos??'—')}</td><td>${esc(d.nick||d.name||'—')}<small style="display:block">${esc(d.transponder??'')}</small></td><td>${esc(d.result_status||'—')}</td><td class="r">${esc(d.n_lap??d.laps?.length??0)}</td><td class="r">${fmt(d.elapsed_ms)}</td><td class="r">${fmt(d.best_ms)}</td></tr>`).join('')}</tbody></table></div><a class="btn sm" href="/api/races/${encodeURIComponent(r.race_id)}/pdf${token?'?t='+encodeURIComponent(token):''}" download>Unduh PDF sesi</a></details>`).join(''):'');
  $('eventResults').innerHTML=content;
  $('eventHistoryDetail').innerHTML=selected?.status==='completed'?`<h2 style="font-size:17px">${esc(selected.name)}</h2><p class="event-muted">${esc(selected.date||'—')} · ${esc(selected.track_name)}</p>${content}`:'<div class="empty"><b>Pilih event selesai</b>Hasil dan klasemen akan tampil di sini.</div>';
}
function renderLive(){
  const live=selected?.stages.find(s=>s.status==='live');
  if(!live){$('eventLive').innerHTML='<p class="note">Tidak ada sesi Event yang sedang berjalan.</p>';return;}
  const rows=race?.klasemen||[];
  $('eventLive').innerHTML=`<p><b>${esc(live.name)}</b><br><span class="event-status">${esc(race?.status||'memuat')}</span></p><div class="event-board"><table><thead><tr><th>Pos</th><th>Driver</th><th class="r">Lap</th><th class="r">Best</th></tr></thead><tbody>${rows.map((d,i)=>`<tr><td>${esc(d.pos??i+1)}</td><td>${esc(d.nick||d.name)}</td><td class="r">${esc(d.n_lap??d.laps?.length??0)}</td><td class="r">${fmt(d.best_ms)}</td></tr>`).join('')}</tbody></table></div><p class="event-muted">Timing menggunakan decoder dan mesin race YurLaps.</p>`;
}
function render(){
  renderTabs();renderList();renderCreate();
  $('eventTitle').textContent=selected?.name||'Pilih event';
  $('eventState').textContent=selected?.status||'';
  $('eventMeta').textContent=selected?`${selected.date||'—'} · ${selected.track_name} · ${selected.classes.length} kelas · ${selected.entries.length} peserta`:'';
  $('eventSync').innerHTML=!selected?'':sync?.conflict?'<p style="color:var(--bad)">Event berubah di aplikasi dan website. Pilih versi yang dipertahankan.</p><div class="actions"><button class="btn sm" data-event-resolve="remote">Pakai versi website</button><button class="btn sm danger" data-event-resolve="local">Kirim versi aplikasi</button></div>':sync?.pending?'Perubahan menunggu sinkronisasi':'Tersinkron';
  const actions=$('eventActions');
  actions.innerHTML=!selected?'':selected.status==='draft'?'<button class="btn primary" data-event-status="registration">Buka pendaftaran</button>':selected.status==='registration'?'<button class="btn primary" data-event-status="running">Mulai event</button>':selected.status==='running'?'<button class="btn" data-event-status="completed">Tutup event</button>':'';
  renderEntries();renderStages();renderResults();renderLive();
}
async function mutate(path,body){try{await api(path,body);await load(selected?.id);}catch(error){fail(error);}}

document.querySelectorAll('[data-event-tab]').forEach(button=>button.onclick=()=>{tab=button.dataset.eventTab;renderTabs();});
document.addEventListener('click',async event=>{
  const select=event.target.closest('[data-event-select]');
  if(select){try{await load(select.dataset.eventSelect);tab=select.closest('#eventHistoryList')?'history':'active';renderTabs();}catch(error){fail(error);}return;}
  const status=event.target.closest('[data-event-status]');
  if(status&&selected){await mutate('/api/event/status',{event_id:selected.id,status:status.dataset.eventStatus});return;}
  const resolve=event.target.closest('[data-event-resolve]');
  if(resolve&&selected){
    const choice=resolve.dataset.eventResolve;
    if(!window.confirm(choice==='local'?'Versi aplikasi akan mengganti perubahan website untuk Event ini. Lanjutkan?':'Versi website akan mengganti perubahan Event lokal. Lanjutkan?'))return;
    await mutate('/api/event/resolve',{event_id:selected.id,choice});return;
  }
  const entry=event.target.closest('[data-entry-action]');
  if(entry&&selected){
    const action=entry.dataset.entryAction,body={event_id:selected.id,entry_id:entry.dataset.id};
    if(action==='remove')await mutate('/api/event/entry/remove',body);
    else if(action==='reconcile')await mutate('/api/event/entry/reconcile',body);
    else{if(action==='approve')body.approved=true;else body.checked_in=action==='checkin';await mutate('/api/event/entry/set',body);}
    return;
  }
  const stage=event.target.closest('[data-stage-action]');
  if(stage&&selected){
    if(stage.dataset.stageAction==='save'&&!window.confirm('Akhiri sesi timing dan simpan hasilnya?'))return;
    await mutate('/api/event/stage/'+stage.dataset.stageAction,{event_id:selected.id,stage_id:stage.dataset.id});
  }
});
$('eventEntryMember').onchange=renderCars;
$('eventSyncNow').onclick=async()=>{
  try{
    const response=await api('/api/cloud/sync',{});
    if(!response.cloud?.user_id)throw Error('Login ke akun YurLaps untuk mengambil Event dari website.');
    $('eventSyncNow').disabled=true;
    $('eventSyncNow').textContent='Sinkronisasi…';
    for(const delay of [1500,3000,5000]){
      await new Promise(resolve=>setTimeout(resolve,delay));
      await load(selected?.id);
    }
  }catch(error){fail(error);}finally{
    $('eventSyncNow').disabled=false;
    $('eventSyncNow').textContent='Ambil dari website';
  }
};
$('eventStageClass').onchange=renderStageEntries;
$('eventStageStart').onchange=()=>{$('eventStageRank').value=$('eventStageStart').value==='grid'?'position':'result';};
$('eventStageKind').onchange=()=>{
  if($('eventStageKind').value==='qualifying')$('eventStageRank').value='best';
};
$('eventStagePreset').onchange=()=>{
  const preset=presets.find(p=>p.id===$('eventStagePreset').value);
  if(!preset)return;
  $('eventStageTarget').value=preset.target;
  $('eventStageValue').value=preset.target==='time'?Math.ceil((preset.target_time_ms||preset.value*1000)/1000):preset.value;
  $('eventStageStart').value=preset.start_mode;
  $('eventStageRank').value=preset.rank==='lapsTime'?'result':preset.rank;
  $('eventStageCountdown').value=preset.cd;
  $('eventStageDelay').value=preset.random_delay_max||2;
  $('eventStageStep').value=preset.countdown_step_ms||1500;
  $('eventStageGap').value=(preset.stagger_gap_ms||10000)/1000;
  $('eventStageStagger').value=preset.stagger_mode||'manual';
  $('eventStageFinishLap').checked=!!preset.finish_current_lap;
};
$('eventCreateForm').onsubmit=async event=>{
  event.preventDefault();
  try{
    const response=await api('/api/event/create',{name:$('eventName').value,date:$('eventDate').value,
      track_id:$('eventTrack').value,format:$('eventFormat').value,best_legs:Number($('eventBestLegs').value),
      points_table:$('eventPoints').value,
      classes:$('eventClasses').value.split(',').map(x=>x.trim()).filter(Boolean)});
    await load(response.event.id);tab='active';renderTabs();$('eventCreateForm').reset();
  }catch(error){fail(error);}
};
$('eventEntryForm').onsubmit=async event=>{
  event.preventDefault();if(!selected)return;
  await mutate('/api/event/entry/add',{event_id:selected.id,class_id:$('eventEntryClass').value,
    member_id:$('eventEntryMember').value,transponder:$('eventEntryCar').value});
};
$('eventStageForm').onsubmit=async event=>{
  event.preventDefault();if(!selected)return;
  const target=$('eventStageTarget').value,value=Number($('eventStageValue').value);
  const cfg={name:$('eventStageName').value,target,value,rank:$('eventStageRank').value,
    start_mode:$('eventStageStart').value,cd:Number($('eventStageCountdown').value),
    random_delay_max:Number($('eventStageDelay').value),
    countdown_step_ms:Number($('eventStageStep').value),
    stagger_mode:$('eventStageStagger').value,stagger_gap_ms:Number($('eventStageGap').value)*1000,
    finish_current_lap:$('eventStageFinishLap').checked,
    target_time_ms:target==='time'?value*1000:null};
  await mutate('/api/event/stage/add',{event_id:selected.id,class_id:$('eventStageClass').value,
    kind:$('eventStageKind').value,name:$('eventStageName').value,group:$('eventStageGroup').value,
    cfg,entry_ids:[...$('eventStageEntries').querySelectorAll('input:checked')].map(input=>input.value)});
};
window.YurLapsEventPage={open:()=>load().catch(fail)};
timer=setInterval(async()=>{
  if($('view-event').style.display==='none'||!selected?.stages.some(s=>s.status==='live'))return;
  try{const detail=await api('/api/event/detail?id='+encodeURIComponent(selected.id));race=detail.race;renderLive();}catch(_error){}
},1000);
})();
