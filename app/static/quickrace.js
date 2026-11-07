/* ============================================================================
   YurLaps — timing UI. Local APIs own persistence, decoder timing and cloud sync.
   ========================================================================== */
(function(){
'use strict';

const BASE = (window.__YURLAPS_CFG__ && window.__YURLAPS_CFG__.base) || '';
const ACCESS_QUERY = new URLSearchParams(window.location.search).get('t');
const $ = id => document.getElementById(id);
const SVG_PLAY = '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5l12 7-12 7z"/></svg>';
const pad = n => String(n).padStart(2,'0');
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

/* ---------- format ---------- */
function fmtLap(ms){
  if(ms == null || !isFinite(ms)) return '—';
  ms=Math.round(ms);
  const m = Math.floor(ms / 60000), s = Math.floor(ms / 1000) % 60, r = ms % 1000;
  return m ? `${m}:${pad(s)}.${String(r).padStart(3,'0')}` : `${s}.${String(r).padStart(3,'0')}`;
}
function fmtClock(ms){
  ms = Math.max(0, ms || 0);
  const m = Math.floor(ms / 60000), s = Math.floor(ms / 1000) % 60, d = Math.floor((ms % 1000) / 100);
  return `${pad(m)}:${pad(s)}.${d}`;
}
function fmtTgl(ts){
  if(!ts) return '—';
  const d = new Date(ts > 1e11 ? ts : ts * 1000);
  return pad(d.getDate()) + ' ' + ['Jan','Feb','Mar','Apr','Mei','Jun','Jul','Agu','Sep','Okt','Nov','Des'][d.getMonth()]
    + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
}
/* Kecepatan passing: jarak antarkabel (m) / duration (µs). */
function kmh(widthM, passingUs){
  if(!(widthM > 0) || !(passingUs > 0)) return null;
  return Math.round(widthM * 3600000 / passingUs * 100) / 100;
}

function rata(a){ return a.length ? a.reduce((x,y) => x+y, 0) / a.length : null; }
function sigma(a){
  if(a.length < 2) return null;
  const m = rata(a);
  return Math.sqrt(a.reduce((s,v) => s + (v-m)*(v-m), 0) / a.length);
}
function mmss(v){
  if(v == null || v === '') return '—';
  if(v >= 60) return Math.round(v/60) + ' menit';
  return v + ' detik';
}

/* ============================================================================
   API
   ========================================================================== */
/* Rute yang memakai POST walau tanpa badan permintaan. Tanpa daftar ini,
   fetch memakai GET dan rute POST akan menjawab 404. */
const RUTE_POST = ['/api/race/track','/api/race/cfg','/api/race/peserta',
  '/api/race/start','/api/race/go','/api/race/pass','/api/race/finish',
  '/api/race/reset','/api/race/resume','/api/race/save','/api/race/discard','/api/race/upload',
  '/api/track','/api/track/delete','/api/tracks/sync','/api/preset','/api/preset/delete'];
async function api(path, body){
  const opt = { headers: {} };
  const route=path.split('?')[0];
  // LAN/browser launches may protect the local API with ?t=TOKEN. Preserve
  // that token for every fetch so the UI does not silently look empty.
  if (ACCESS_QUERY && !/[?&]t=/.test(path)) path += (path.includes('?') ? '&' : '?') + 't=' + encodeURIComponent(ACCESS_QUERY);
  if(body !== undefined || RUTE_POST.includes(route)){
    opt.method = 'POST';
    opt.headers['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(body === undefined ? {} : body);
  }
  let r;
  try{
    r = await fetch(BASE + path, opt);
  }catch(e){
    throw new Error('Tidak bisa menghubungi layanan lokal YurLaps.');
  }
  let j = {};
  try{ j = await r.json(); }catch(e){}
  if(!r.ok || j.ok === false){
    throw new Error(j.error || ('Permintaan gagal (' + r.status + ')'));
  }
  return j;
}

/* ============================================================================
   STATE LOKAL UI
   ========================================================================== */
const DATA = { club:null, ringkasan:{}, track:[], member:[], preset:[] };
const state = { trackId:null, presetId:null, peserta:[] };
let sesi = null;        // snapshot dari server
let dirty = false;
let toastTimer = null;

function toast(msg){
  const t = $('toast');
  t.textContent = msg;
  t.classList.add('on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('on'), 2200);
}
function gagal(e){ toast(e && e.message ? e.message : String(e)); }

function trackAktif(){
  if(sesi && sesi.track && sesi.track.id === state.trackId) return sesi.track;
  return DATA.track.find(t => t.id === state.trackId) || DATA.track[0] || null;
}
function presetAktif(){
  return DATA.preset.find(p => p.id === state.presetId) || DATA.preset[0] || null;
}
function sedangJalan(){
  return sesi && ['live','countdown','paused'].includes(sesi.status);
}

/* ============================================================================
   MUAT DATA
   ========================================================================== */
async function muatAwal(){
  const b = await api('/api/club/bootstrap');
  DATA.club = b.club || {};
  DATA.ringkasan = b.ringkasan || {};
  DATA.track = b.tracks || [];
  DATA.member = b.members || [];
  DATA.preset = b.presets || [];
  sesi = b.race;
  state.trackId = sesi.track_id || (DATA.track[0] && DATA.track[0].id) || null;
  const c = sesi.cfg || {};
  const cocok = DATA.preset.find(p => p.name === c.nama);
  state.presetId = cocok ? cocok.id : ((DATA.preset[0] && DATA.preset[0].id) || null);
  pulihkanPeserta();
  renderAll();
  renderHistory();
}

/* Peserta sesi di server -> bentuk yang dipakai daftar peserta. */
function pulihkanPeserta(){
  state.peserta = (sesi.peserta || []).map((p,i) => ({
    uid:p.uid, memberId:p.member_id || '', name:p.name, nick:p.nick, kelas:p.kelas,
    tp:p.transponder == null ? '' : String(p.transponder), no:p.no || i+1, luar:!!p.luar, ttsName:p.tts_name||''
  }));
}

async function segarkanSesi(){
  const r = await api('/api/race/state');
  sesi = r.race;
  if(window.YurLapsEvents) window.YurLapsEvents.race(sesi);
  return sesi;
}

/* ============================================================================
   NAVIGASI
   ========================================================================== */
const VIEWS = ['practice','quickrace','history','member','track','decoder','sound'];
function show(v){
  VIEWS.forEach(k => { const s = $('view-' + k); if(s) s.style.display = (k === v) ? '' : 'none'; });
  document.querySelectorAll('.tab').forEach(b => b.classList.toggle('on', b.dataset.view === v));
  window.scrollTo(0,0);
  if(v === 'history') renderHistory();
  if(v === 'practice'){renderPractice();refreshPractice();}
  if(v === 'member') renderMember();
  if(v === 'track') renderTrackPage();
  if(v === 'decoder'){ segarkanDecoder(); renderDecoderLog(); }
  if(v === 'sound' && window.YurLapsSound) window.YurLapsSound.load();
}
document.querySelectorAll('.tab').forEach(t => t.onclick = () => show(t.dataset.view));

/* ============================================================================
   RENDER: TRACK + PRESET + CHIPS
   ========================================================================== */
function renderTrackSelect(){
  const sel = $('selTrack');
  sel.innerHTML = DATA.track.map(t =>
    `<option value="${t.id}">${esc(t.name)}</option>`).join('');
  sel.value = state.trackId || '';
  sel.disabled = !!sesi && sesi.status!=='idle';
  const t = trackAktif();
  if(!t){ $('topTrack').textContent = '—'; return; }
  $('topTrack').textContent = t.name;
  $('footTrack').textContent = t.name;
  const nSesi = t.n_race || 0;
  $('trackMeta').innerHTML =
    `<b>${nSesi} sesi di track ini</b><span>Rekor: ${fmtLap(t.best_ms)} · ${esc(t.holder || '—')}`
    + (t.loop_width_m ? ` · lebar loop ${t.loop_width_m} m` : ' · lebar loop belum diisi') + `</span>`;
  $('trackSrc').textContent = t.synced ? 'dari website' : 'lokal · belum sinkron';
}
function renderPresetSelect(){
  const sel = $('selPreset');
  sel.innerHTML = DATA.preset.map(p =>
    `<option value="${p.id}">${esc(p.name)}</option>`).join('');
  sel.value = state.presetId || '';
  sel.disabled=!!sesi&&sesi.status!=='idle';
  $('gearBtnTop').disabled=!!sesi&&sesi.status!=='idle';
}
function renderChips(){
  const c = (sesi && sesi.cfg) || {};
  const target = c.target === 'time'
    ? `waktu ${mmss(c.value)}`
    : `${c.value} lap`;
  const rank = ({position:'Posisi lintasan', result:'Result · lap / total', lapsTime:'Result · lap / total', best:'Best lap', top3:'Rata² 3 lap tercepat',
    consecutive:'3 lap berurutan'})[c.rank] || c.rank;
  const mulai = c.start_mode === 'grid' ? 'grid bersamaan' : 'bergiliran';
  const chips = [
    ['Target', target], ['Urutan', rank], ['Mulai', mulai],
    ['Minimum lap', ((trackAktif()?.min_lap_ms||1500)/1000)+' detik'], ['Timing', 'YurLaps Decoder']
  ];
  let html = chips.map(([k,v]) => `<span class="chip">${k}: <b>${esc(v)}</b></span>`).join('');
  const t = trackAktif();
  if(t && !t.loop_width_m) html += `<span class="chip warn">Lebar loop belum diisi — km/h tidak dihitung</span>`;
  if(!state.peserta.length) html += `<span class="chip warn">Belum ada peserta</span>`;
  $('chips').innerHTML = html;
  $('qrTitle').textContent = (c.nama || 'Quick Race') + (t ? ' · ' + t.name : '');
}

/* ============================================================================
   RENDER: PAPAN SKOR (dari server)
   ========================================================================== */
const previousLaps=new Map();
function renderBoard(){
  const body = $('boardBody');
  const kosong = $('boardEmpty'), tabel = $('board');
  const baris = (sesi && sesi.klasemen) || [];
  if(!baris.length){
    kosong.style.display = ''; tabel.style.display = 'none';
    $('detailCard').hidden = true;
    return;
  }
  kosong.style.display = 'none'; tabel.style.display = '';
  const positions=new Map([...body.querySelectorAll('tr.row')].map(row=>[row.dataset.uid,row.getBoundingClientRect().top]));
  body.innerHTML=baris.map((p,i)=>{
    const flash=previousLaps.has(p.uid)&&p.n_lap>previousLaps.get(p.uid);previousLaps.set(p.uid,p.n_lap);
    return `<tr class="row ${state.selectedUid===p.uid?'sel':''} ${flash?'lap-flash':''}" data-uid="${esc(p.uid)}">${resultCells(p,i)}</tr>`;
  }).join('');

  body.querySelectorAll('tr.row').forEach(tr => {
    const top=positions.get(tr.dataset.uid),delta=top==null?0:top-tr.getBoundingClientRect().top;
    if(delta&&tr.animate&&!window.matchMedia('(prefers-reduced-motion: reduce)').matches)tr.animate([{transform:`translateY(${delta}px)`},{transform:'none'}],{duration:280,easing:'ease-out'});
    tr.onclick = () => {state.selectedUid=tr.dataset.uid;renderBoard();renderDetail();};
  });
}

function renderDetail(){
  const p = ((sesi && sesi.klasemen) || []).find(x => x.uid === state.selectedUid)
         || ((sesi && sesi.klasemen) || [])[0];
  const card = $('detailCard');
  if(!p){ card.hidden = true; return; }
  card.hidden = false;
  $('dNama').textContent = p.no;
  $('dAv').textContent = (p.nick || p.name).slice(0,2).toUpperCase();
  $('dPanggil').textContent = p.nick || p.name;
  $('dLengkap').textContent = p.transponder == null
    ? `${p.name} · transponder belum dipetakan`
    : `${p.name} · TP ${p.transponder}`;
  $('dLaps').textContent = p.n_lap;
  $('dTopSpeed').textContent = speed(p.top_speed_kmh);
  $('dAverageSpeed').textContent = speed(p.average_speed_kmh);
  $('dBest').textContent = fmtLap(p.best_ms);
  $('dAvg').textContent = fmtLap(p.rata_ms);
  $('dSig').textContent = p.sigma_ms == null ? '—' : (p.sigma_ms/1000).toFixed(3);
  const lap = p.laps || [];
  const passes=(p.passings||[]).filter(x=>x.lap_ms!=null);
  $('dLapList').innerHTML='<div class="table-scroll"><table><thead><tr><th>Lap</th><th>Waktu</th><th>RSSI</th><th>Hit</th><th>Passing µs</th><th>Speed</th></tr></thead><tbody>'+lap.map((ms,i)=>({ms,i,passing:passes[i]||{}})).reverse().map(({ms,i,passing:v})=>`<tr><td>L${i+1}</td><td class="${ms===p.best_ms?'best':'num'}">${fmtLap(ms)}</td><td>${numeric(v.rssi,1)}</td><td>${numeric(v.hit_count)}</td><td>${numeric(v.pass_duration_us)}</td><td>${speed(v.speed_kmh)}</td></tr>`).join('')+'</tbody></table></div>';
  // grafik sederhana: batang per lap
  const maks = Math.max(...lap, 1);
  $('dGraph').setAttribute('points',lap.map((ms,i)=>`${lap.length<2?120:i*240/(lap.length-1)},${44-ms/maks*40}`).join(' '));
}

function renderLog(){
  const log=sesi?.log||[];
  $('pLogN').textContent=log.length+' terakhir';
  const labels={origin:'Start clock',lap:'Lap tercatat',finished:'Sudah finish',ignored:'Diabaikan'};
  $('pLog').innerHTML=log.length?'<div class="table-scroll"><table><thead><tr><th>Driver / TP</th><th>Status</th><th>RSSI</th><th>Hit</th><th>Passing µs</th><th>Clock ms</th></tr></thead><tbody>'+log.map(e=>`<tr class="${e.skip?'skip':''}"><td class="nm">${esc(e.nama)}<small>TP ${esc(e.transponder)}</small></td><td title="${esc(e.alasan||'')}">${esc(e.alasan||labels[e.status]||(e.skip?'Diabaikan':'Lap '+e.lap))}</td><td class="num">${numeric(e.rssi,1)}</td><td class="num">${numeric(e.hit_count)}</td><td class="num">${numeric(e.dur_us)}</td><td class="num">${numeric(e.decoder_timecode)}</td></tr>`).join('')+'</tbody></table></div>':'<div class="note">Belum ada passing pada tampilan ini. Setiap passing, termasuk yang diabaikan, dicatat di sini.</div>';
}

/* ============================================================================
   PRACTICE (data nyata dari recorder lokal; tidak ada fixture/demo)
   ========================================================================== */
const practiceData = { state: null, history: [] };
let practiceRefreshBusy = false;
function practiceStats(laps){
  const a = (laps || []).map(Number).filter(v => Number.isFinite(v) && v > 0);
  if (!a.length) return {laps:0,total:null,best:null,top3:null,cons:null,p80:null,consistency:null};
  const sorted = a.slice().sort((x,y)=>x-y), avg = rata(a), sd = sigma(a);
  const cons = a.length < 3 ? null : Math.min(...Array.from({length:a.length-2},(_,i)=>rata(a.slice(i,i+3))));
  return {laps:a.length,total:a.reduce((s,v)=>s+v,0),best:sorted[0],top3:a.length>=3?rata(sorted.slice(0,3)):null,cons,
    p80:rata(sorted.slice(0, Math.ceil(a.length * .8))),consistency:Math.max(0,100*(1-(sd||0)/avg))};
}
function sessionLapsByTp(session){
  if (session?.laps && !Array.isArray(session.laps)) return session.laps;
  if (session?.laps_ms && !Array.isArray(session.laps_ms)) return session.laps_ms;
  return Object.fromEntries((session?.drivers || []).map(d=>[String(d.transponder ?? d.transponder_id),d.laps_ms || d.laps || []]));
}
function practiceRows(session){
  const frozen = new Map((session?.drivers || []).map(d=>[String(d.transponder ?? d.transponder_id),d]));
  return Object.entries(sessionLapsByTp(session)).map(([tp,laps])=>{
    // Never reassign old results to the current owner of a transponder.
    const driver = frozen.get(tp) || {transponder:tp,name:`TP ${tp}`};
    const passings = driver.passings || [];
    const speeds = passings.map(p=>p.speed_kmh).filter(v=>v!=null&&Number.isFinite(Number(v))).map(Number);
    return {...practiceStats(laps),tp,driver,session,lapTimes:laps,passings,
      name:driver.nickname||driver.nick||driver.driver||driver.name||`TP ${tp}`,
      fullName:driver.driver||driver.name||`Transponder ${tp}`,avatar:driver.avatar_url||'',
      topSpeed:driver.top_speed_kmh ?? (speeds.length?Math.max(...speeds):null)};
  });
}
function numeric(v, decimals=0){return v==null||!Number.isFinite(Number(v))?'—':Number(v).toFixed(decimals);}
function practiceDetail(item, tp){
  const d=practiceRows(item).find(row=>String(row.tp)===String(tp));if(!d)return;
  reportRaceId=null;
  $('sesiTitle').textContent=d.name+' · Practice';
  $('sesiSub').textContent=`${item.track_name||'Track'} · ${fmtTgl(item.started_at)}${item.ended_at?' – '+fmtTgl(item.ended_at):' · sesi aktif'} · ${item.sync==='terkirim'?'tersinkron':item.ended_at?'menunggu sinkronisasi':'mencatat latihan'}`;
  const passes=d.passings.length?d.passings:d.lapTimes.map((ms,i)=>({seq:i+1,lap_ms:ms}));
  $('sesiBody').innerHTML=`<div class="practice-detail-banner"><b>${esc(d.fullName)} · TP ${esc(d.tp)}</b><span>Privasi dikelola pemilik akun di website. Practice tidak masuk rekor race track.</span></div>
    <div class="metrics practice-metrics">${[['Laps',d.laps],['Total time',fmtLap(d.total)],['Best lap',fmtLap(d.best)],['Top3 avg',fmtLap(d.top3)],['Top3 consecutive',fmtLap(d.cons)],['P80 avg',fmtLap(d.p80)],['Consistency',d.consistency==null?'—':d.consistency.toFixed(1)+'%'],['Top speed',speed(d.topSpeed)]].map(([label,value])=>`<div class="metric"><span>${label}</span><b>${value}</b></div>`).join('')}</div>
    <h3>Detail setiap lap</h3><div class="report-table"><table><thead><tr><th>Lap</th><th>Waktu lap</th><th>Selisih best</th><th>Waktu passing</th><th>RSSI · dB</th><th>Hit</th><th>Passing · µs</th><th>Speed</th></tr></thead><tbody>${passes.map((p,i)=>`<tr><td>${p.origin?'Start':p.seq??i+1}</td><td class="${p.lap_ms===d.best?'best':'num'}">${fmtLap(p.lap_ms)}</td><td class="num">${p.lap_ms!=null&&d.best!=null?'+'+((p.lap_ms-d.best)/1000).toFixed(3):'—'}</td><td class="num">${p.at?esc(fmtTgl(p.at)):p.decoder_timecode!=null?numeric(p.decoder_timecode)+' ms':'—'}</td><td class="num">${numeric(p.rssi,1)}</td><td class="num">${numeric(p.hit_count)}</td><td class="num">${numeric(p.pass_duration_us)}</td><td class="num">${speed(p.speed_kmh)}</td></tr>`).join('')||'<tr><td colspan="8" class="empty">Passing pertama membuka clock. Menunggu lap pertama.</td></tr>'}</tbody></table></div>`;
  $('sesiPdf').style.display='none';$('modalSesi').hidden=false;
}
function renderPractice(){
  const ps=practiceData.state||{}, active=ps.session||null;
  const sessions=Array.isArray(ps.active_sessions)?ps.active_sessions:active?[active]:[];
  const rows=sessions.flatMap(practiceRows).sort((a,b)=>(a.best??Infinity)-(b.best??Infinity));
  $('practiceTrack').textContent=active?.track_name||trackAktif()?.name||'Pilih track di menu Track';
  $('practiceDrivers').textContent=rows.length;$('practiceLaps').textContent=rows.reduce((n,d)=>n+d.laps,0);
  $('practiceSync').textContent=accountData.syncing?'Sinkronisasi…':accountData.online?'Online · otomatis':'Tersimpan di perangkat';
  $('practiceLive').innerHTML=ps.active?'<i></i> Otomatis aktif':sesi?.status!=='idle'?'Dijeda selama race':'Menunggu track aktif';
  $('practiceWindow').textContent=`Sesi ditutup setelah ${Number(ps.idle_seconds||120)} detik tanpa passing driver`;
  $('practiceBadge').textContent=rows.length;$('practiceBadge').classList.toggle('on',rows.length>0);
  $('practiceBody').innerHTML=rows.length?rows.map((d,i)=>`<tr class="row" data-practice-live="${i}"><td class="nm"><span class="rank-dot ${['gold','silver','bronze'][i]||''}">${i+1}</span> ${esc(d.name)}<small>TP ${esc(d.tp)}</small></td><td class="num r">${d.laps}</td><td class="num r">${fmtLap(d.total)}</td><td class="best r">${fmtLap(d.best)}</td><td class="num r">${fmtLap(d.top3)}</td><td class="num r">${fmtLap(d.cons)}</td><td class="num r">${fmtLap(d.p80)}</td><td class="num r">${d.consistency==null?'—':d.consistency.toFixed(1)+'%'}</td><td class="num r">${speed(d.topSpeed)}</td></tr>`).join(''):'<tr><td colspan="9" class="empty"><b>Belum ada practice aktif</b>Passing pertama membuka sesi saat track aktif dan race tidak berjalan.</td></tr>';
  $('practiceBody').querySelectorAll('[data-practice-live]').forEach(b=>b.onclick=()=>{const d=rows[Number(b.dataset.practiceLive)];practiceDetail(d.session,d.tp);});
  const hist=(practiceData.history||[]).filter(s=>s.ended_at!=null);
  const groups=new Map();
  hist.forEach((s,si)=>practiceRows(s).forEach(d=>{const key=String(d.driver.driver_id||d.driver.user_id||d.driver.member_id||'tp-'+d.tp);if(!groups.has(key))groups.set(key,{key,name:d.name,full:d.fullName,avatar:d.avatar,sessions:[]});groups.get(key).sessions.push({session:s,tp:d.tp,laps:d.laps,index:si});}));
  const opened=new Set([...$('practiceSessions').querySelectorAll('details[open]')].map(el=>el.dataset.memberKey));
  $('practiceSessionCount').textContent=hist.length+' sesi · '+groups.size+' member';
  $('practiceSessions').innerHTML=groups.size?[...groups.values()].map(g=>`<details class="practice-member" data-member-key="${esc(g.key)}" ${opened.has(g.key)?'open':''}><summary>${g.avatar&&/^https?:/.test(g.avatar)?`<img class="avatar" src="${esc(g.avatar)}" alt="" referrerpolicy="no-referrer">`:`<span class="avatar">${esc(g.name.slice(0,2).toUpperCase())}</span>`}<span><b>${esc(g.name)}</b><small>${g.sessions.length} sesi latihan</small></span></summary>${g.sessions.map(({session:s,tp,laps,index})=>`<button class="practice-session" data-practice-index="${index}" data-practice-tp="${esc(tp)}"><span><b>${esc(s.track_name||'Sesi practice')}</b><span>${esc(fmtTgl(s.started_at))} · ${laps} lap · ${s.sync==='terkirim'?'tersinkron':'menunggu sinkronisasi'}</span></span><span class="session-arrow">›</span></button>`).join('')}</details>`).join(''):'<div class="empty"><b>Belum ada sesi selesai</b>Sesi tersimpan per driver setelah jeda idle, pergantian track, atau race dimulai.</div>';
  $('practiceSessions').querySelectorAll('[data-practice-index]').forEach(b=>b.onclick=()=>practiceDetail(hist[Number(b.dataset.practiceIndex)],b.dataset.practiceTp));
  $('practiceClock').textContent=new Date().toLocaleTimeString('id-ID',{hour:'2-digit',minute:'2-digit'});
}
async function refreshPractice(){
  if(practiceRefreshBusy)return;practiceRefreshBusy=true;
  try{const [a,h]=await Promise.all([api('/api/practice/state'),api('/api/practice/history')]);practiceData.state=a.practice||null;practiceData.history=h.sessions||[];renderPractice();}
  catch(e){$('practiceSync').textContent='Layanan lokal belum terhubung';}
  finally{practiceRefreshBusy=false;}
}

/* ============================================================================
   COUNTDOWN + START + FINISH
   ========================================================================== */
let racePollBusy=false;
async function mulaiCountdown(){
  if(!state.peserta.length){toast('Pilih peserta dulu');openPeserta();return;}
  if(sesi?.status!=='idle'){toast('Simpan atau buang sesi lalu Reset terlebih dahulu');return;}
  try{
    sesi=(await api('/api/race/peserta',{peserta:state.peserta.map(p=>({member_id:p.memberId,name:p.name,nick:p.nick,kelas:p.kelas,transponder:p.tp===''?null:Number(p.tp)}))})).race;
    pulihkanPeserta();
    const missing=(sesi.peserta||[]).filter(p=>!String(p.tts_name||'').trim());
    if(missing.length){toast('Isi nama TTS terlebih dahulu: '+missing.map(p=>p.nick||p.name).join(', '));show('member');return;}
    sesi=(await api('/api/race/start')).race;renderAll();renderRaceStatus();
  }catch(e){gagal(e);}
}
function renderRaceStatus(){
  if(!sesi)return;
  const st=sesi.status;
  const target=sesi.cfg?.target==='time'?sesi.cfg.value*1000:0;
  $('qrClock').textContent=target&&sesi.cfg.start_mode==='grid'?fmtClock(Math.max(0,target-(sesi.elapsed_ms||0))):fmtClock(sesi.elapsed_ms||0);
  const label={idle:'SIAP',countdown:'COUNTDOWN',live:'LIVE',paused:'TIMING DIJEDA',done:'SELESAI'}[st]||st;
  $('liveTag').className=st==='live'?'live':'live idle';$('liveTag').innerHTML='<i></i>'+label;
  $('cdLayer').hidden=st!=='countdown';
  if(st==='countdown'){
    $('cdNum').textContent=String(sesi.countdown_value??sesi.cfg.cd);
    $('cdWho').textContent=`${trackAktif()?.name||'Track'} · ${state.peserta.length} pembalap`;
    $('cdCap').textContent='Bersiap';
  }
}
async function refreshRace(){
  if(racePollBusy)return;racePollBusy=true;
  const before=sesi?.status;
  try{
    await segarkanSesi();renderBoard();renderDetail();renderLog();renderTombol();renderRaceStatus();
    if(before!=='done'&&sesi.status==='done')selesaiTampil();
  }catch(e){$('qrLead').textContent=e.message;}finally{racePollBusy=false;}
}

async function selesai(alasan){
  if(!sesi || !['live','paused','countdown'].includes(sesi.status)) return;
  try{
    await api('/api/race/finish', { alasan: alasan || 'dihentikan operator' });
    sesi = await segarkanSesi();
  }catch(e){ return gagal(e); }
  $('cdLayer').hidden=true;
  selesaiTampil();
}

function bukaFinish(){
  if(!sesi || !['live','paused','countdown'].includes(sesi.status)) return;
  $('modalFinish').hidden = false;
}
async function finishAndSave(){
  $('modalFinish').hidden = true;
  await selesai('dihentikan operator');
}
async function finishAndDiscard(){
  $('modalFinish').hidden = true;
  try{
    await api('/api/race/finish', { alasan:'dibuang operator' });
    const r = await api('/api/race/discard');
    sesi = r.race;
    $('cdLayer').hidden=true;
    renderAll(); toast('Sesi race dibuang');
  }catch(e){ gagal(e); }
}

function selesaiTampil(){
  $('cdLayer').hidden=true;
  $('liveTag').className = 'live idle';
  $('liveTag').innerHTML = '<i></i>SELESAI';
  $('asideDec').textContent = 'aktif · menunggu lintasan';
  renderAll();
  toast('Sesi selesai · ' + (sesi.alasan_selesai || ''));
  if(!sesi.saved_id&&!sesi.discarded)bukaSave();
}

async function resetSesi(diam){
  if(!sesi?.can_reset){toast('Simpan atau buang sesi sebelum Reset');return;}
  try{
    const r = await api('/api/race/reset');
    sesi = r.race;
  }catch(e){ return gagal(e); }
  $('cdLayer').hidden=true;
  $('qrClock').textContent = '00:00.0';
  $('liveTag').className = 'live idle';
  $('liveTag').innerHTML = '<i></i>SIAP';
  pulihkanPeserta();previousLaps.clear();state.selectedUid=null;
  renderAll();
  if(!diam) toast('Sesi direset');
}

function renderTombol(){
  const st=sesi?.status||'idle', locked=st!=='idle', paused=st==='paused';
  const unresolved=st==='done'&&!sesi?.can_reset;
  $('btnStart').disabled=locked||!state.presetId||!trackAktif();
  $('btnStart').style.display=st==='idle'?'':'none';
  $('btnStart').innerHTML=SVG_PLAY+' Start';
  $('btnResume').style.display=paused&&sesi.start_decoder_ts!=null?'':'none';
  $('btnResume').disabled=false;
  $('btnReset').style.display=sesi?.can_reset?'':'none';
  $('btnFinish').style.display=['live','countdown','paused'].includes(st)?'':'none';
  $('btnFinish').disabled=!['live','countdown','paused'].includes(st);
  $('btnReview').style.display=unresolved?'':'none';
  ['btnPeserta','selTrack','selPreset','gearBtnTop','accountClub','btnTrackAdd'].forEach(id=>$(id).disabled=locked);
  $('menuLogout').disabled=locked;
  $('qrLead').textContent=paused?'Timing dilanjutkan tanpa menunggu status decoder.'
    :st==='live'?'Sesi berjalan. Lap tercatat dari decoder.'
    :st==='countdown'?'Countdown berjalan. Leaderboard tetap terlihat.'
    :st==='done'?sesi.can_reset?(sesi.saved_id?'Sesi tersimpan.':'Sesi dibuang.')+' Tekan Reset untuk menyiapkan race berikutnya.':'Sesi selesai. Pilih Simpan / buang sesi sebelum Reset.'
    :!trackAktif()?'Buat dan aktifkan track di menu Track.'
    :!state.presetId?'Buat preset melalui tombol gear sebelum memulai.'
    :state.peserta.length?'Siap. Tekan Start untuk memulai countdown.':'Pilih peserta, lalu Start.';
}
$('btnResume').onclick = async () => {
  try { sesi = (await api('/api/race/resume')).race; renderAll();renderRaceStatus(); }
  catch (e) { gagal(e); }
};

/* ============================================================================
   PESERTA
   ========================================================================== */
function openPeserta(){
  pulihkanPeserta();
  $('rSearch').value = '';
  renderRoster();
  $('modalPeserta').hidden = false;
}
function renderRoster(){
  const q = ($('rSearch').value || '').toLowerCase();
  const box = $('rRows');
  const dipilih = new Map(state.peserta.map(p => [p.memberId, p]));
  const rows = DATA.member.filter(m => {
    if(!q) return true;
    return (m.name + ' ' + m.nick + ' ' + (m.transponder || []).join(' '))
      .toLowerCase().includes(q);
  });
  box.innerHTML = rows.map(m => {
    const on = dipilih.has(m.id);
    const p = dipilih.get(m.id);
    const tps = m.transponder || [];
    const tp = p ? p.tp : (tps[0] == null ? '' : tps[0]);
    const no = p ? p.no : '';
    const sub = m.status === 'pending' ? '<span class="st-p pending">menunggu</span>'
      : esc((m.mobil || '—') + ' · ' + (m.role || 'member'));
    return `<div class="rrow ${on ? 'on' : ''}" data-id="${m.id}">
      <input type="checkbox" ${on ? 'checked' : ''} ${m.status==='pending'?'disabled':''} data-chk>
      <span class="nm"><b>${esc(m.name)}</b><small>${sub}${tps.length > 1
        ? ' · ' + tps.length + ' transponder' : ''}${!m.tts_name?' · TTS belum diisi':''}</small></span>
      <input type="number" min="1" max="99" value="${no}" placeholder="no" data-no>
      <input type="text" value="${esc(p ? p.kelas : (m.kelas || ''))}" data-kelas>
      <input type="text" value="${esc(tp)}" placeholder="transponder" data-tp>
    </div>`;
  }).join('') || '<div class="note" style="padding:14px">Tidak ada member yang cocok.</div>';

  $('rInfo').textContent = state.peserta.length + ' dipilih';

  box.querySelectorAll('.rrow').forEach(row => {
    const id = row.dataset.id;
    const m = DATA.member.find(x => x.id === id);
    const chk = row.querySelector('[data-chk]');
    const inpNo = row.querySelector('[data-no]');
    const inpTp = row.querySelector('[data-tp]');
    const inpKelas = row.querySelector('[data-kelas]');
    [...row.querySelectorAll('input')].forEach(inp =>
      inp.onclick = e => e.stopPropagation());

    chk.onchange = () => {
      if(chk.checked){
        const tps = m.transponder || [];
        const tp = inpTp.value.trim() || (tps[0] == null ? '' : String(tps[0]));
        const no = Number(inpNo.value) || (state.peserta.length + 1);
        state.peserta.push({ uid:'', memberId:m.id, name:m.name, nick:m.nick,
          kelas: inpKelas.value.trim() || m.kelas || '', tp, no, luar: false });
      } else {
        state.peserta = state.peserta.filter(p => p.memberId !== m.id);
      }
      renderRoster();
    };
    const ubah = () => {
      const p = state.peserta.find(x => x.memberId === id);
      if(!p) return;
      p.tp = inpTp.value.trim() || p.tp;
      p.no = Number(inpNo.value) || p.no;
      p.kelas = inpKelas.value.trim() || p.kelas;
    };
    inpTp.onchange = ubah; inpNo.onchange = ubah; inpKelas.onchange = ubah;
  });
}
$('rSearch').oninput = renderRoster;
$('rAll').onclick = () => {
  DATA.member.forEach(m => {
    if(m.status==='pending')return;
    // satu member hanya boleh muncul sekali di daftar peserta
    if(state.peserta.some(p => p.memberId === m.id)) return;
    const tps = m.transponder || [];
    state.peserta.push({ uid:'', memberId:m.id, name:m.name, nick:m.nick,
      kelas:m.kelas || '', tp: tps[0] == null ? '' : String(tps[0]),
      no: state.peserta.length + 1, luar: false });
  });
  renderRoster();
};
$('rNone').onclick = () => { state.peserta = []; renderRoster(); };
$('rCancel').onclick = () => {pulihkanPeserta();$('modalPeserta').hidden = true;};
$('rSave').onclick = async () => {
  state.peserta.sort((a,b) => a.no - b.no).forEach((p,i) => p.no = i+1);
  try{
    const r = await api('/api/race/peserta', { peserta: state.peserta.map(p => ({
      member_id:p.memberId, no:p.no, name:p.name, nick:p.nick, kelas:p.kelas,
      transponder:p.tp === '' ? null : Number(p.tp), luar:p.luar
    })) });
    sesi = r.race;
  }catch(e){ return gagal(e); }
  pulihkanPeserta();
  $('modalPeserta').hidden = true;
  renderAll();
  toast(state.peserta.length + ' peserta disiapkan');
};

/* ============================================================================
   PRESET: satu editor untuk buat, ubah, duplikat, dan hapus.
   ========================================================================== */
let editPresetId=null;
const rankHelp={
  position:'Posisi lintasan: lap terbanyak lalu urutan melintasi garis pada clock global. Finish Grid dengan penilaian ini menetapkan posisi finish.',
  result:'Result: lap terbanyak lalu total waktu driver terendah. Total dihitung dari passing pertama driver, cocok untuk Staggered.',
  best:'Best lap: satu lap penuh tercepat. Cocok untuk qualifying; lap pembuka tidak dihitung.',
  top3:'Top 3 laps average: rata-rata tiga lap penuh tercepat. Driver baru memiliki nilai setelah menyelesaikan tiga lap.',
  consecutive:'Top 3 consecutive laps: rata-rata terbaik dari tiga lap penuh berurutan. Mengutamakan pace yang konsisten.'
};
function presetConfig(p){
  const v=p||{};
  return {name:v.name||v.nama||'Quick Race',target:v.target||'laps',value:v.value||10,
    target_time_ms:v.target_time_ms||(v.target==='time'?(v.value||180)*1000:null),
    rank:v.rank==='lapsTime'?'result':(v.rank||(v.start_mode==='staggered'?'result':'position')),
    start_mode:v.start_mode||'grid',cd:v.cd||5,random_delay_max:v.random_delay_max||1,
    finish_current_lap:!!v.finish_current_lap,stagger_mode:v.stagger_mode==='auto'?'auto':'manual',
    stagger_gap_ms:v.stagger_gap_ms||10000,sound:v.sound===0?0:1};
}
function pilihChip(wrapId,v){document.querySelectorAll('#'+wrapId+' button').forEach(b=>b.classList.toggle('on',b.dataset.v===String(v)));}
function targetChoice(){return document.querySelector('#setTarget button.on')?.dataset.v||'laps';}
function isiNilai(target,value){
  const el=$('setValue');
  el.type=target==='time'?'text':'number';
  if(target==='time'){
    el.removeAttribute('min');el.removeAttribute('step');el.inputMode='numeric';el.placeholder='05:00';
    el.value=pad(Math.floor(Number(value||180)/60))+':'+pad(Number(value||180)%60);
  }else{el.min='1';el.step='1';el.inputMode='numeric';el.value=value||10;}
  $('setTargetLbl').textContent=target==='time'?'Target waktu (menit:detik)':'Target lap';
  $('setValueHelp').textContent=target==='time'?'Contoh: 05:30 untuk 5 menit 30 detik.':'Masukkan jumlah lap penuh. Passing pertama membuka clock; passing kedua menghasilkan lap 1.';
  $('finishCurrentField').hidden=target!=='time';
}
function presetExplanations(){
  const stagger=$('setStart').value==='staggered';
  $('staggerField').hidden=!stagger;
  $('staggerGapField').hidden=!stagger||$('setStagger').value!=='auto';
  $('setStartHelp').textContent=stagger?'Driver start satu per satu. Passing pertama membuka clock pribadi dan tidak dihitung sebagai lap.':'Semua driver start pada GO. Lap time dihitung dari passing pertama setiap driver agar adil; passing pertama bukan lap 1.';
  $('setRankHelp').textContent=rankHelp[$('setRank').value]||'';
}
function fillPreset(p){
  const c=presetConfig(p);
  $('setName').value=c.name; pilihChip('setTarget',c.target); isiNilai(c.target,c.value);
  $('setRank').value=c.rank;$('setStart').value=c.start_mode;$('setCd').value=c.cd;
  $('setRandom').value=c.random_delay_max;$('setFinishCurrent').checked=c.finish_current_lap;
  $('setStagger').value=c.stagger_mode;$('setStaggerGap').value=c.stagger_gap_ms/1000;
  presetExplanations();
}
function renderEditPresetSelect(){
  $('editPresetSelect').innerHTML=(editPresetId?'':'<option value="">Preset baru</option>')+DATA.preset.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('');
  $('editPresetSelect').value=editPresetId||'';
  $('presetDelete').disabled=!editPresetId||DATA.preset.length<=1;
  $('presetDuplicate').disabled=!editPresetId;
}
function bukaSetting(id=state.presetId){
  if(sesi&&sesi.status!=='idle'){toast('Reset sesi lebih dahulu untuk mengubah preset');return;}
  editPresetId=id||null;
  const p=DATA.preset.find(x=>x.id===editPresetId)||presetConfig(sesi&&sesi.cfg);
  fillPreset(p);renderEditPresetSelect();$('modalSetting').hidden=false;
}
$('gearBtnTop').onclick=()=>bukaSetting();
$('editPresetSelect').onchange=()=>{editPresetId=$('editPresetSelect').value||null;fillPreset(DATA.preset.find(p=>p.id===editPresetId));renderEditPresetSelect();};
$('presetNew').onclick=()=>{editPresetId=null;fillPreset({name:'Preset baru',target:'laps',value:10,rank:'position'});renderEditPresetSelect();$('setName').focus();};
$('presetDuplicate').onclick=()=>{const c=readPresetForm();if(!c)return;editPresetId=null;fillPreset({...c,name:c.name+' (salinan)'});renderEditPresetSelect();$('setName').focus();};
$('presetDelete').onclick=async()=>{
  const p=DATA.preset.find(x=>x.id===editPresetId);if(!p||DATA.preset.length<=1)return;
  if(!confirm('Hapus preset "'+p.name+'"? Hasil race lama tetap tersimpan.'))return;
  try{const r=await api('/api/preset/delete',{id:p.id});DATA.preset=r.presets||[];state.presetId=DATA.preset[0]?.id||null;await applyPreset(state.presetId);bukaSetting(state.presetId);toast('Preset dihapus');}catch(e){gagal(e);}
};
$('setCancel').onclick=()=>{$('modalSetting').hidden=true;};
document.querySelectorAll('#setTarget button').forEach(b=>b.onclick=()=>{pilihChip('setTarget',b.dataset.v);isiNilai(b.dataset.v,b.dataset.v==='time'?180:10);});
$('setStart').onchange=()=>{$('setRank').value=$('setStart').value==='grid'?'position':'result';presetExplanations();};
$('setStagger').onchange=presetExplanations;$('setRank').onchange=presetExplanations;
function readPresetForm(){
  const target=targetChoice(),raw=$('setValue').value.trim();let value;
  if(target==='time'){
    const match=/^(\d{1,4}):([0-5]\d)$/.exec(raw);
    if(!match){toast('Isi waktu dengan format menit:detik, misalnya 05:30');$('setValue').focus();return null;}
    value=Number(match[1])*60+Number(match[2]);
  }else value=Number(raw);
  const cd=Number($('setCd').value),random=Number($('setRandom').value),gap=Number($('setStaggerGap').value);
  if(!Number.isInteger(value)||value<1){toast('Target harus lebih dari nol');return null;}
  if(!Number.isInteger(cd)||cd<1||cd>60){toast('Countdown harus 1 sampai 60 detik');return null;}
  if(!Number.isInteger(random)||random<1||random>3){toast('Random delay maksimum harus 1 sampai 3 detik');return null;}
  if(!Number.isFinite(gap)||gap<1){toast('Jeda panggilan minimal 1 detik');return null;}
  const name=$('setName').value.trim();if(!name){toast('Nama preset wajib diisi');return null;}
  return {name,target,value,target_time_ms:target==='time'?value*1000:null,cd,random_delay_max:random,
    finish_current_lap:$('setFinishCurrent').checked,rank:$('setRank').value,start_mode:$('setStart').value,
    stagger_mode:$('setStagger').value,stagger_gap_ms:Math.round(gap*1000),sound:1};
}
$('presetForm').onsubmit=async e=>{
  e.preventDefault();const body=readPresetForm();if(!body)return;
  if(editPresetId)body.id=editPresetId;
  $('setSave').disabled=true;
  try{const r=await api('/api/preset',body);DATA.preset=r.presets||[];state.presetId=r.preset.id;await applyPreset(state.presetId);$('modalSetting').hidden=true;toast('Preset disimpan dan aktif');}catch(err){gagal(err);}finally{$('setSave').disabled=false;}
};
async function applyPreset(id){
  const p=DATA.preset.find(x=>x.id===id);if(!p)return;
  const r=await api('/api/race/cfg',presetConfig(p));sesi=r.race||await segarkanSesi();state.presetId=p.id;renderAll();
}
$('selPreset').onchange=async()=>{try{await applyPreset($('selPreset').value);toast('Preset aktif: '+(presetAktif()?.name||''));}catch(e){$('selPreset').value=state.presetId||'';gagal(e);}};

/* ============================================================================
   TRACK
   ========================================================================== */
function renderTrackPage(){
  const wrap = $('trackWrap');
  wrap.innerHTML = DATA.track.map(t => `
    <div class="trackrow${t.active?' active':''}" data-id="${t.id}">
      <span class="ico"><svg viewBox="0 0 24 24"><path d="M5 5c-4 0-4 14 0 14h4c4 0 2-6 6-6h3c5 0 5-8 0-8z"/></svg></span>
      <span class="who"><b>${esc(t.name)} ${t.active?'<span class="badge-sync ya">Aktif</span>':''} ${t.synced
        ? '<span class="badge-sync ya">tersinkron</span>'
        : '<span class="badge-sync belum">belum sinkron</span>'}</b>
        <span>${esc(t.addr || '—')}${t.loop_width_m ? ' · lebar loop ' + t.loop_width_m + ' m' : ' · lebar loop belum diisi'} · min lap ${Number(t.min_lap_ms||1500)/1000} detik</span></span>
      <span class="rec"><b>${fmtLap(t.best_ms)}</b>${esc(t.holder || '—')}</span>
      <span class="act">
        <button class="btn sm" data-act="activate" ${t.active?'disabled':''}>Aktifkan</button><button class="btn sm" data-act="edit">Edit</button>
        <button class="btn sm icon danger" data-act="del" title="Hapus">×</button>
      </span>
    </div>`).join('') || '<div class="empty"><b>Belum ada track</b>Tekan “Tambah track”.</div>';

  wrap.querySelectorAll('.trackrow').forEach(row => {
    const id = row.dataset.id;
    row.querySelectorAll('button').forEach(b => b.onclick = async () => {
      if(sesi&&sesi.status!=='idle'){toast('Selesaikan dan reset race sebelum mengubah track');return;}
      if(b.dataset.act === 'edit') return bukaTrack(id);
      if(b.dataset.act === 'activate'){try{const t=DATA.track.find(x=>x.id===id);const r=await api('/api/track',{...t,active:true});DATA.track=r.tracks;state.trackId=id;await api('/api/race/track',{track_id:id});sesi=await segarkanSesi();renderAll();toast('Track aktif diperbarui');}catch(e){gagal(e);}return;}
      if(DATA.track.length <= 1){ toast('Minimal satu track harus ada'); return; }
      if(!confirm('Hapus track ini? Sesi lama tetap tersimpan dengan nama track-nya.')) return;
      try{
        const r = await api('/api/track/delete', { id });
        DATA.track = r.tracks;
        if(state.trackId === id){
          state.trackId = (DATA.track[0] || {}).id;
          await api('/api/race/track', { track_id: state.trackId });
          sesi = await segarkanSesi();
        }
      }catch(e){ return gagal(e); }
      renderTrackPage(); renderTrackSelect(); renderAll();
      toast('Track dihapus');
    });
  });

  $('chipTrackN').textContent = DATA.track.length + ' track';
  $('chipTrackSync').textContent = DATA.track.filter(t => !t.synced).length + ' belum disinkronkan';
  $('mTrack').textContent = DATA.track.length;
}

let editTrackId = null;
function bukaTrack(id){
  if(sesi&&sesi.status!=='idle'){toast('Reset sesi sebelum mengubah track');return;}
  editTrackId = id || null;
  const t = id ? DATA.track.find(x => x.id === id) : null;
  $('trkTitle').textContent = t ? 'Edit track' : 'Tambah track';
  $('trkName').value = t ? t.name : '';
  $('trkAddr').value = t ? (t.addr || '') : '';
  $('trkWidth').value = t ? (t.loop_width_m || '') : '';
  $('trkNote').value = t ? (t.note || '') : '';
  $('trkMinLap').value=Number(t?.min_lap_ms||1500)/1000;
  $('trkActive').checked=t?!!t.active:DATA.track.length===0;
  hitungTrack();
  $('modalTrack').hidden = false;
}
function hitungTrack(){
  const w = Number($('trkWidth').value);
  $('trkHitung').innerHTML = w
    ? `Jarak antarkabel <b>${w} m</b> · passing 90.000 µs → <b>${kmh(w,90000)} km/h</b>`
    : 'Isi lebar loop untuk melihat contoh kecepatan.';
}
$('trkWidth').oninput = hitungTrack;
$('trkCancel').onclick = () => { $('modalTrack').hidden = true; };
$('trkSave').onclick = async () => {
  const name = $('trkName').value.trim();
  if(!name){ toast('Nama track belum diisi'); $('trkName').focus(); return; }
  if(!Number.isFinite(Number($('trkMinLap').value))||Number($('trkMinLap').value)<0.1){toast('Minimum lap time harus minimal 0,1 detik');return;}
  if($('trkWidth').value&&!(Number($('trkWidth').value)>0)){toast('Lebar loop harus lebih dari nol');return;}
  const body = { name, addr: $('trkAddr').value.trim(),
    loop_width_m: $('trkWidth').value || null, note: $('trkNote').value.trim(), min_lap_s:Number($('trkMinLap').value), active:$('trkActive').checked };
  if(editTrackId) body.id = editTrackId;
  try{
    const r = await api('/api/track', body);
    DATA.track = r.tracks;
    if(r.track.active){
      state.trackId = r.track.id;
      if(!sedangJalan()) await api('/api/race/track', { track_id: r.track.id });
      sesi = await segarkanSesi();
    }
  }catch(e){ return gagal(e); }
  const baru = !editTrackId;
  $('modalTrack').hidden = true;
  editTrackId = null;
  renderTrackPage(); renderTrackSelect(); renderAll();
  toast(baru ? 'Track ditambahkan' : 'Track diperbarui');
};
$('btnTrackAdd').onclick = () => bukaTrack(null);
$('btnTrackSync').onclick = async () => {
  try{
    await api('/api/tracks/sync', {});
  }catch(e){
    // 409 = belum ada koneksi; tetap beri tahu apa yang terjadi
    toast(e.message);
  }
};

/* ============================================================================
   MEMBER + DECODER
   ========================================================================== */
function renderMember(){
  const q = ($('memberSearch').value || '').toLowerCase();
  const list = DATA.member.filter(m =>
    !q || (m.name + ' ' + m.nick + ' ' + (m.transponder||[]).join(' ')).toLowerCase().includes(q));
  $('memberList').innerHTML = list.map(m => `
    <div class="member">
      ${m.avatar_url&&/^https?:/.test(m.avatar_url)?`<img class="avatar" src="${esc(m.avatar_url)}" alt="" referrerpolicy="no-referrer">`:`<span class="avatar">${esc((m.nick || m.name).slice(0,2).toUpperCase())}</span>`}
      <span class="who"><b>${esc(m.nick || m.name)}</b><span>${esc(m.name)}</span></span>
      <span class="tps">${(m.transponder||[]).length
        ? m.transponder.map(t=>{const car=(m.transponder_details||[]).find(v=>String(v.transponder_id)===String(t));return `<span class="tp">${esc(t)}${car?' · '+esc([car.rc_brand,car.rc_class].filter(Boolean).join(' / ')):''}</span>`;}).join('')
        : '<span style="font-size:10px">transponder belum dipetakan</span>'}<br>${
        esc([m.mobil, m.kelas, m.tts_name?'TTS: '+m.tts_name:'TTS belum diisi'].filter(Boolean).join(' · '))}</span>
      <span class="role ${esc(m.role)}">${m.source==='guest'?'tamu akun':['website','cloud'].includes(m.source)?'akun club':'offline'}</span>${['cloud','website','guest'].includes(m.source)?'<span class="note">Kelola di website</span>':`<button class="btn sm" data-edit-member="${esc(m.id)}">Edit</button><button class="btn sm danger" data-delete-member="${esc(m.id)}">Hapus</button>`}
      <span class="st-p ${m.status}">${m.status === 'active' ? 'aktif' : 'menunggu'}</span>
    </div>`).join('') || '<div class="empty">Tidak ada member yang cocok.</div>';
  $('memberCount').textContent = list.length + ' tampil';
  const clubName = (DATA.club && DATA.club.name) || 'Club';
  if ($('memberClubName')) $('memberClubName').textContent = clubName;
  $('mActive').textContent  = DATA.member.filter(m => m.status === 'active').length;
  $('mPending').textContent = DATA.member.filter(m => m.status === 'pending').length;
  $('mTp').textContent = DATA.member.reduce((s,m) => s + (m.transponder||[]).length, 0);
  $('mTrack').textContent = DATA.track.length;
}
$('memberSearch').oninput = renderMember;

let decOnline = false;
let decoderBusy = false;
async function segarkanDecoder(){
  if(decoderBusy) return;
  decoderBusy = true;
  try{
    const st = await api('/api/state');
    const d = st.decoder || {}, status = d.status || {}, manager = d.manager || {};
    decOnline = !!d.online;
    $('decDev').textContent = 'YurLaps Decoder';
    $('topDecoder').className='pill '+(decOnline?'online':'offline');
    $('topDecoderText').textContent=decOnline?'Decoder terhubung':'Decoder tidak terhubung';
    $('decLed').textContent = decOnline ? '● Data diterima' : manager.running ? '● Proses aktif · belum ada data' : '○ Tidak terhubung';
    $('decLed').style.color = decOnline ? '#168657' : '#9b6a19';
    $('decRate').textContent = (d.counters || {}).P || 0;
    $('decNoise').textContent = decOnline && Number.isFinite(status.noise_power) ? status.noise_power.toFixed(1) : '—';
    $('decDiagnostic').textContent = decOnline
      ? `Data YurLaps Decoder masuk · ${d.counters.P || 0} passing · ${d.counters.S || 0} status. Timestamp ${status.timecode ?? '—'} ms.`
      : manager.running ? 'YurLaps Decoder berjalan dan menunggu data. Periksa koneksi USB; sambungan dicoba kembali otomatis.'
      : (manager.last_error || 'Sambungkan YurLaps Decoder. Aplikasi akan menyalakan dan menghubungkannya otomatis.');
    const g = Number(d.gain ?? 20);
    $('decGain').textContent = g.toFixed(1) + ' dB';
    gainTersimpan = g;
    if(document.activeElement !== $('gainRange') && !gainSibuk){$('gainRange').value=g;$('gainVal').textContent=g.toFixed(1)+' dB';}
    $('gainRange').disabled = sedangJalan(); $('btnDecStart').disabled = sedangJalan();
    const passes = (d.passes || []).slice().reverse();
    $('decLogN').textContent = passes.length + ' terakhir';
    $('decLogBody').innerHTML = passes.map(p=>`<tr><td class="mono">${esc(p.transponder_id)}</td><td class="nm">${esc(p.driver || 'Belum terdaftar')}</td>
      <td class="num r">${p.rssi == null ? '—' : Number(p.rssi).toFixed(1)}</td>
      <td class="num r">${p.hit_count ?? '—'}</td><td class="num r">${p.pass_duration_us ?? '—'}</td>
      <td class="num r">${p.decoder_timecode ?? '—'}</td></tr>`).join('') || '<tr><td colspan="6" class="empty">Belum ada passing. Transponder yang belum terdaftar juga akan muncul di sini.</td></tr>';
    const values = (d.noise_history || []).map(x=>x.noise).filter(Number.isFinite);
    if(values.length){const low=Math.min(...values)-2, high=Math.max(...values)+2;
      $('noiseChart').innerHTML=`<text x="8" y="18" fill="#64748b" font-size="11">${high.toFixed(1)} dBFS</text><text x="8" y="102" fill="#64748b" font-size="11">${low.toFixed(1)} dBFS</text><polyline fill="none" stroke="#278bc7" stroke-width="2" points="${values.map((v,i)=>`${80+i*800/Math.max(1,values.length-1)},${100-(v-low)/(high-low)*90}`).join(' ')}"/>`;
    }else $('noiseChart').innerHTML='<text x="20" y="60" fill="#64748b">Belum ada status noise dari decoder</text>';
  }catch(e){$('decDiagnostic').textContent=e.message;}finally{decoderBusy=false;}
}
function renderDecoderLog(){segarkanDecoder();}
function catatDecoder(){segarkanDecoder();}
setInterval(segarkanDecoder, 1000);

/* Gain: geser = angka di layar; lepas = konfirmasi lalu simpan.
   Tanpa jeda/debounce: satu gerakan = satu konfirmasi (sebelumnya konfirmasi
   bisa muncul dua kali karena nilai dikirim dari dua jalur). */
let gainSibuk = false;
let gainTersimpan = null;

$('gainRange').oninput = e => {
  $('gainVal').textContent = Number(e.target.value).toFixed(1) + ' dB';
  if(gainTersimpan == null || Number(e.target.value) !== Number(gainTersimpan)){
    $('gainStatus').textContent = 'belum disimpan';
  }
};

$('gainRange').onchange = async e => {
  const nilai = Number(e.target.value);
  if(gainSibuk) return;
  if(gainTersimpan != null && nilai === Number(gainTersimpan)){
    if(!$('gainStatus').textContent.startsWith('disimpan')){
      $('gainStatus').textContent = 'tidak berubah';
    }
    return;
  }
  if(sesi && sesi.status === 'live'){
    toast('Sesi sedang berjalan — gain tidak diubah. Selesaikan sesi dulu.');
    // kembalikan tampilan ke nilai yang benar-benar tersimpan
    if(gainTersimpan != null){
      $('gainRange').value = gainTersimpan;
      $('gainVal').textContent = Number(gainTersimpan).toFixed(1) + ' dB';
    }
    $('gainStatus').textContent = 'tidak diubah saat sesi berjalan';
    return;
  }
  const ya = confirm('Ubah gain ke ' + nilai.toFixed(1) + ' dB?\n\n'
    + 'YurLaps Decoder akan dijalankan ulang dengan gain baru. '
    + 'Sebaiknya lakukan saat tidak ada pembalap di lintasan.');
  if(!ya){ segarkanDecoder(); return; }

  gainSibuk = true;
  $('gainStatus').textContent = 'menyimpan…';
  try{
    const r = await api('/api/decoder/gain', { gain: nilai });
    gainTersimpan = Number(r.gain == null ? nilai : r.gain);
    $('gainStatus').textContent = r.restarted
      ? 'disimpan · decoder dijalankan ulang'
      : 'disimpan · decoder tidak sedang dikelola YurLaps';
    toast(r.message || ('Gain ' + nilai.toFixed(1) + ' dB disimpan'));
  }catch(err){
    $('gainStatus').textContent = 'gagal disimpan';
    gagal(err);
  }finally{
    gainSibuk = false;
  }
  setTimeout(segarkanDecoder, 1200);
};

$('btnDecStart').onclick = async () => {
  try{
    await api('/api/decoder/restart', {});
    toast('YurLaps mencoba menyalakan ulang decoder…');
  }catch(e){ gagal(e); }
  setTimeout(segarkanDecoder, 1200);
};

/* ============================================================================
   HISTORY
   ========================================================================== */
let historyCache = [];
async function renderHistory(){
  let list = [];
  try{
    const r = await api('/api/races');
    list = r.races || [];
  }catch(e){ gagal(e); }
  historyCache = list;
  renderHistFilter();
  $('histBadge').textContent = list.length;

  const q = $('histFilter').value || 'semua';
  const tampil = list.filter(r2 => q === 'semua' || r2.track_id === q);
  $('histWrap').style.display = tampil.length ? 'none' : '';
  $('hist').style.display = tampil.length ? '' : 'none';
  $('histCount').textContent = tampil.length + ' sesi';

  const tb = $('histBody');
  tb.innerHTML = tampil.map(r2 => {
    const badge = r2.sync === 'terkirim'
      ? '<span class="st-live">tersinkron</span>'
      : '<span class="st-out">menunggu sinkronisasi</span>';
    const durasi = r2.elapsed_ms || 0;
    return `<tr class="row" data-id="${r2.id}">
      <td class="nm">${esc(r2.name)}<small>${fmtTgl(r2.started_at)}</small></td>
      <td>${esc(r2.track_name || '—')}</td>
      <td class="num r">${r2.n_driver || 0}</td>
      <td class="num r">${r2.n_lap || 0}</td>
      <td class="best r">${fmtLap(r2.best)}</td>
      <td class="num r">${fmtClock(durasi)}</td>
      <td class="r">${badge}</td>
      <td class="r"><button class="btn sm danger" data-act="hapus">Hapus</button></td>
    </tr>`;
  }).join('');

  tb.querySelectorAll('tr.row').forEach(tr => {
    tr.onclick = e => {
      if(e.target.dataset.act === 'hapus') return hapusHistory(tr.dataset.id);
      bukaSesi(tr.dataset.id);
    };
  });
  renderHistRekap(list);
}

function renderHistRekap(list){
  const semuaLap = list.reduce((s,r2) => s + (r2.n_lap || 0), 0);
  const terbaik = Math.min(...list.filter(r2 => r2.best).map(r2 => r2.best).concat([Infinity]));
  const belum = list.filter(r2 => r2.sync !== 'terkirim').length;
  $('rTotal').textContent = list.length;
  $('rBelum').textContent = belum;
  $('rSent').textContent = list.length - belum;
  $('rLap').textContent = semuaLap;
  $('histRekap').innerHTML = list.length
    ? `<span class="chip">Best: <b>${fmtLap(isFinite(terbaik) ? terbaik : null)}</b></span>`
      + `<span class="chip">Terakhir: <b>${esc(list[0].name)}</b></span>`
    : '<span class="chip">Belum ada sesi tersimpan.</span>';
}

async function unggah(id){
  try{
    await api('/api/race/upload', { id });
  }catch(e){ toast(e.message); }
}
async function hapusHistory(id){
  if(!confirm('Hapus sesi ini dari perangkat dan website?')) return;
  try{
    await api('/api/races/delete', {id});
    historyCache = historyCache.filter(x=>x.id!==id);
    renderHistory(); toast('Sesi dihapus dan sinkronisasi penghapusan dijadwalkan');
  }catch(e){
    toast(e.message || 'Sesi tidak dapat dihapus');
  }
}

async function bukaSesi(id){
  let r;
  try{
    const res = await api('/api/races/' + encodeURIComponent(id));
    r = res.race;
  }catch(e){ return gagal(e); }
  $('sesiTitle').textContent = r.name;
  $('sesiPdf').style.display='';
  const durasi = r.end_decoder_ts != null && r.start_decoder_ts != null ? Math.max(0,r.end_decoder_ts-r.start_decoder_ts) : 0;
  $('sesiSub').textContent = `${r.track_name || '—'} · ${fmtTgl(r.started_at)} · durasi ${fmtClock(durasi)}`
    + ` · ${r.drivers.length} pembalap · ${r.drivers.reduce((s,d) => s + d.laps.length, 0)} lap`
    + ` · ${r.sync === 'terkirim' ? 'tersinkron' : 'menunggu sinkronisasi'}`;
  reportRaceId = id;
  const ds=r.drivers, count=Math.max(0,...ds.map(d=>d.laps.length));
  $('sesiBody').innerHTML = `<p class="note">Acuan GO decoder: ${r.start_decoder_ts ?? 'tidak tersedia (arsip lama)'} ms · ${esc(r.finish_reason || '')}. Data lokal.</p>
    <h3>Results</h3><div class="report-table"><table><thead><tr><th>Pos</th><th>Chg</th><th>Driver</th><th>Gap ∑</th><th>Gap Δ</th><th>Laps</th><th>Total</th><th>Best lap</th><th>Top3 avg</th><th>Top3 consecutive</th><th>P80 avg</th><th>Consistency</th><th>Top speed</th></tr></thead><tbody>`
    + ds.map((d,i)=>`<tr>${resultCells(d,i)}</tr>`).join('')+'</tbody></table></div>'
    + '<h3 style="margin-top:24px">Lap times</h3><div class="report-table"><table><thead><tr><th>Lap</th>'
    + ds.map(d=>`<th>${esc(d.nick||d.name)}</th>`).join('')+'</tr></thead><tbody>'
    + Array.from({length:count},(_,i)=>`<tr><td>${i+1}</td>${ds.map(d=>`<td class="num" style="color:${d.laps[i]===d.best_ms?'#c43e2e':'inherit'}">${fmtLap(d.laps[i])}</td>`).join('')}</tr>`).join('')+'</tbody></table></div>'
    + reportCharts(ds,r.cfg||{})
    + '<p class="note">P80 avg: rata-rata 80% lap tercepat (dibulatkan ke atas). Consistency: 100 × (1 − σ/rata-rata). Passing pertama membuka clock; statistik dihitung dari lap penuh.</p>'
    + '<h3>Detail pembalap</h3>'+ds.map(d=>`<details class="side-card"><summary>${esc(d.nick||d.name)}</summary><p>Top speed: ${speed(d.top_speed_kmh)} · Average speed: ${speed(d.average_speed_kmh)}</p><p class="note">Dari passing duration dan jarak antarkabel ${r.loop_width_m ?? '—'} m.</p></details>`).join('');
  $('modalSesi').hidden = false;
}
$('sesiClose').onclick = () => { $('modalSesi').hidden = true; };
$('histFilter').onchange = renderHistory;

/* ============================================================================
   SIMPAN RACE -> HISTORY
   ========================================================================== */
function nomorSesiBaru(){
  return (sesi && sesi.cfg ? sesi.cfg.nama : 'Quick Race') + ' #' + (historyCache.length + 1);
}
function bukaSave(){
  const kal = (sesi && sesi.klasemen) || [];
  const totalLap = kal.reduce((s,p) => s + p.n_lap, 0);
  $('saveName').value = nomorSesiBaru();
  $('saveSub').textContent = 'Sesi dihentikan (' + (sesi.alasan_selesai || 'operator') + '). '
    + 'Sesi akan masuk ke History Race.';
  $('saveTrack').textContent = (trackAktif() || {}).name || '—';
  $('saveTrack').title = (trackAktif() || {}).name || '';
  $('saveSum').textContent = kal.length + ' pembalap · ' + totalLap + ' lap';
  $('saveTop3').innerHTML = kal.slice(0,3).map((p,i) =>
    `${i+1}. <b>${esc(p.nick || p.name)}</b> — ${p.n_lap} lap`
    + (p.best_ms ? ' · best ' + fmtLap(p.best_ms) : '')).join('<br>')
    || 'Belum ada lap tercatat.';
  $('modalSave').hidden = false;
}
$('saveCancel').onclick = () => { $('modalSave').hidden = true; };
$('saveDrop').onclick = async () => {
  if(!confirm('Buang sesi ini? Lap yang tercatat akan hilang.')) return;
  $('modalSave').hidden = true;
  try{
    const r = await api('/api/race/discard');
    sesi = r.race;
  }catch(e){ return gagal(e); }
  pulihkanPeserta();
  renderAll();
  toast('Sesi dibuang. Tekan Reset untuk race berikutnya.');
};
$('saveOk').onclick = async () => {
  const kal = (sesi && sesi.klasemen) || [];
  if(!kal.reduce((s,p) => s + p.n_lap, 0)
     && !confirm('Belum ada lap tercatat. Tetap simpan sesi kosong?')) return;
  try{
    const r = await api('/api/race/save', { nama: $('saveName').value.trim() });
    toast('Tersimpan: ' + r.tersimpan.nama);
  }catch(e){ return gagal(e); }
  $('modalSave').hidden = true;
  await segarkanSesi();
  renderAll();
  await renderHistory();
  toast('Sesi tersimpan dan masuk antrean sinkronisasi. Tekan Reset untuk race berikutnya.');
};

/* ============================================================================
   AKSI TOMBOL QUICK RACE
   ========================================================================== */
$('btnPeserta').onclick = openPeserta;
$('btnReset').onclick = () => resetSesi(false);
$('btnStart').onclick = mulaiCountdown;
$('btnFinish').onclick = bukaFinish;
$('btnReview').onclick=bukaSave;
$('btnClearLog').onclick=async()=>{try{sesi=(await api('/api/race/log/clear',{})).race;renderLog();toast('Tampilan passing log dibersihkan');}catch(e){gagal(e);}};
$('finishCancel').onclick = () => { $('modalFinish').hidden = true; toast('Race dilanjutkan'); };
$('finishSave').onclick = finishAndSave;
$('finishDiscard').onclick = finishAndDiscard;

$('selTrack').onchange = async () => {
  const id = $('selTrack').value;
  if(sesi?.status!=='idle'){ $('selTrack').value = state.trackId; toast('Track tidak bisa diganti saat sesi berjalan'); return; }
  try{
    const r = await api('/api/race/track', { track_id:id });
    sesi = r.race;
    state.trackId = id;
  }catch(e){ return gagal(e); }
  renderTrackSelect(); renderAll();
  toast('Track: ' + ((trackAktif() || {}).name || '—'));
};

/* ============================================================================
   RENDER SEMUA
   ========================================================================== */
function renderAll(){
  renderTrackSelect();
  renderPresetSelect();
  renderChips();
  $('nPeserta').textContent = state.peserta.length;
  renderBoard(); renderDetail(); renderLog(); renderTombol();renderRaceStatus();
  renderMember(); renderTrackPage();
  $('topClub').textContent = (DATA.club && DATA.club.name) || '—';
  const namaClub = (DATA.club && DATA.club.name) || 'Club';
  $('akunNick').textContent = namaClub;
  $('akunAv').textContent = namaClub.slice(0,2).toUpperCase();
  $('akunRole').textContent = (DATA.club && DATA.club.role ? DATA.club.role : 'leader')
    + ' · ' + ((DATA.club && DATA.club.name) || '—');
  if(sesi){
    const belum = (DATA.ringkasan && DATA.ringkasan.belum_terkirim) || 0;
    $('pilltxt').textContent = 'Offline — ' + belum + ' sesi menunggu sinkronisasi';
    if(sesi.status === 'live'){
      $('liveTag').className = 'live';
      $('liveTag').innerHTML = '<i></i>LIVE';
    } else if(sesi.status === 'paused'){
      $('liveTag').className='live idle';$('liveTag').innerHTML='<i></i>TIMING DIJEDA';
    } else if(sesi.status === 'countdown'){
      $('liveTag').className='live idle';$('liveTag').innerHTML='<i></i>COUNTDOWN';
    } else if(sesi.status === 'done'){
      $('liveTag').className = 'live idle';
      $('liveTag').innerHTML = '<i></i>SELESAI';
    } else {
      $('liveTag').className = 'live idle';
      $('liveTag').innerHTML = '<i></i>SIAP';
    }
  }
  renderHistFilter();
  renderAccount();
  renderPractice();
}
function renderHistFilter(){
  const sel = $('histFilter');
  const lama = sel.value;
  const tracks=new Map(DATA.track.map(t=>[t.id,t.name]));
  historyCache.forEach(r=>{if(r.track_id&&!tracks.has(r.track_id))tracks.set(r.track_id,r.track_name||'Track arsip');});
  sel.innerHTML='<option value="semua">Semua track</option>'+[...tracks].map(([id,name])=>`<option value="${esc(id)}">${esc(name)}</option>`).join('');
  sel.value = lama && [...sel.options].some(o => o.value === lama) ? lama : 'semua';
}

async function muatUlangData(){
  const b = await api('/api/club/bootstrap');
  DATA.club = b.club || {};
  DATA.ringkasan = b.ringkasan || {};
  DATA.track = b.tracks || [];
  DATA.member = b.members || [];
  DATA.preset = b.presets || [];
  sesi = b.race;
  if(!DATA.track.some(t => t.id === state.trackId)){
    state.trackId = sesi.track_id || (DATA.track[0] && DATA.track[0].id) || null;
  }
  if(!DATA.preset.some(p => p.id === state.presetId)){
    state.presetId = (DATA.preset[0] && DATA.preset[0].id) || null;
  }
  pulihkanPeserta();
}

let clubRefreshBusy=false;
async function refreshClub(){
  if(clubRefreshBusy||!$('modalPeserta').hidden||!$('modalMember').hidden||!$('modalTrack').hidden||!$('modalSetting').hidden)return;
  clubRefreshBusy=true;
  try{await muatUlangData();renderAll();if($('view-history').style.display!=='none')await renderHistory();}
  catch(e){/* Keep the last complete local snapshot and retry. */}
  finally{clubRefreshBusy=false;}
}

window.YurLapsUI={api,toast,getRace:()=>sesi,getMembers:()=>DATA.member,refreshAccount:()=>refreshAccount()};

/* ---------------------------------------------------------------------------
   Panel akun: OAuth website YurLaps + status sinkronisasi nyata.
   ------------------------------------------------------------------------- */
let accountData = { user_id:null, email:'', selected_club:null, clubs:[], online:false, syncing:false };
let accountBusy = false;
function accountName(){
  if(accountData.nickname||accountData.name)return accountData.nickname||accountData.name;
  if(accountData.email) return accountData.email.split('@')[0].replace(/^./,c=>c.toUpperCase());
  return 'Login';
}
function renderAccount(){
  const logged=!!accountData.user_id;
  const name=logged ? accountName() : 'Login';
  const club=(accountData.clubs||[]).find(c=>c.id===accountData.selected_club) || (DATA.club&&DATA.club.name ? {name:DATA.club.name}:null);
  $('akunNick').textContent=name; $('akunAv').textContent=logged ? name.slice(0,2).toUpperCase() : 'YL';
  $('akunRole').textContent=logged ? ((club&&club.name)||'Pilih club') : 'akun belum masuk';
  $('menuName').textContent=logged ? name : 'Belum login';
  $('menuEmail').textContent=logged ? (accountData.email || 'Akun YurLaps') : 'Masuk untuk sinkronisasi';
  $('menuAvatar').textContent=logged ? name.slice(0,2).toUpperCase() : 'YL';
  ['akunAv','menuAvatar'].forEach(id=>{
    const avatar=$(id);
    avatar.style.backgroundImage=logged&&/^https?:/.test(accountData.avatar_url||'')?'url("'+String(accountData.avatar_url).replace(/["\\\n\r]/g,'')+'")':'';
    avatar.style.backgroundSize='cover';avatar.style.backgroundPosition='center';
    if(avatar.style.backgroundImage)avatar.textContent='';
  });
  $('menuLogin').hidden=logged; $('clubSelectField').hidden=!logged; $('menuLogout').hidden=!logged;
  const choices=accountData.clubs||[];
  $('accountClub').innerHTML='<option value="">Pilih club</option>'+choices.map(c=>`<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('');
  $('accountClub').value=accountData.selected_club||'';
  $('accountClub').disabled=!!sesi&&sesi.status!=='idle';$('menuLogout').disabled=!!sesi&&sesi.status!=='idle';
  $('accountClubHint').textContent=choices.length?'Member, track, dan hasil memakai club ini.':'Belum ada club yang dapat dikelola akun ini.';
  $('pill').classList.toggle('online',!!accountData.online); $('pill').classList.toggle('offline',!accountData.online);
  $('pilltxt').textContent=logged ? (accountData.syncing?'Sinkronisasi…':(accountData.online?'Online · sinkronisasi otomatis':'Offline · data tersimpan lokal')) : 'Offline · login untuk sinkronisasi';
  $('pill').title=accountData.error||'';
}
async function refreshAccount(){
  try{ const r=await api('/api/cloud/status'); accountData=r.cloud||{}; renderAccount(); }
  catch(e){ /* account status unavailable while local server starts */ }
}
$('btnAkun').onclick=e=>{e.stopPropagation();if(!accountData.user_id){$('menuLogin').click();return;}const m=$('accountMenu');m.hidden=!m.hidden;renderAccount();};
$('menuLogin').onclick=async()=>{
  $('accountMenu').hidden=true;
  // CAPTCHA belongs to the website. Always hand off login to the default browser.
  try{
    const r=await api('/api/cloud/login',{});
    if(window.yurlapsShell?.openExternal) await window.yurlapsShell.openExternal(r.url);
    else window.open(r.url,'_blank','noopener,noreferrer');
    toast('Halaman login dibuka di browser. Kembali ke aplikasi setelah berhasil.');
  }catch(e){$('loginError').textContent=e.message; $('modalLogin').hidden=false;}
};
$('loginCancel').onclick=()=> $('modalLogin').hidden=true;
$('loginForm').onsubmit=async e=>{
  e.preventDefault();if(accountBusy)return;accountBusy=true;$('loginSubmit').disabled=true;$('loginError').textContent='';
  try{const r=await api('/api/cloud/password-login',{email:$('loginEmail').value.trim(),password:$('loginPassword').value});
    $('loginPassword').value='';accountData=r.cloud||{};await refreshAccount();await muatUlangData();renderAll();$('modalLogin').hidden=true;toast('Berhasil login');
  }catch(err){$('loginError').textContent=err.message;}finally{accountBusy=false;$('loginSubmit').disabled=false;}
};
$('accountClub').onchange=async()=>{
  const id=$('accountClub').value;if(!id)return;
  try{const r=await api('/api/cloud/select',{club_id:id});accountData=r.cloud||{};await muatUlangData();state.trackId=sesi.track_id;renderAll();await refreshPractice();toast('Club aktif diperbarui');}
  catch(e){$('accountClub').value=accountData.selected_club||'';gagal(e);}
};
$('menuLogout').onclick=async()=>{try{await api('/api/cloud/logout',{});accountData={};$('accountMenu').hidden=true;await muatUlangData();state.trackId=sesi.track_id;renderAll();await refreshPractice();toast('Kamu sudah logout');}catch(e){gagal(e);}};
document.addEventListener('click',e=>{const m=$('accountMenu');if(m&&!m.hasAttribute('hidden')&&!m.contains(e.target)&&e.target!==$('btnAkun'))m.setAttribute('hidden','');});

/* ============================================================================
   MULAI
   ========================================================================== */
window.addEventListener('unhandledrejection', ev => {
  console.error('YurLaps: janji gagal', ev.reason && ev.reason.stack || ev.reason);
});

function speed(v){return v == null ? '—' : Number(v).toFixed(2)+' km/h';}
function fmtGap(v){return !v?'—':v.laps!=null?`${v.laps>0?'+':''}${v.laps}L`:`${v.ms>=0?'+':''}${(v.ms/1000).toFixed(3)}`;}
function resultCells(p,i){return `<td class="pos">${p.pos || i+1}</td><td>${p.change>0?'▲'+p.change:p.change<0?'▼'+Math.abs(p.change):'—'}</td>
<td class="nm">${esc(p.nick || p.name)} ${p.result_status && p.result_status!=="FIN"?`<span class="outside">${esc(p.result_status)}</span>`:""}<small>#${p.no || i+1} · TP ${p.transponder ?? '—'}</small></td>
<td class="num">${fmtGap(p.gap_leader)}</td><td class="num">${fmtGap(p.gap_previous)}</td><td class="num">${p.n_lap ?? (p.laps||[]).length}</td>
${['total_ms','best_ms','top3_ms','consecutive_ms','p80_ms'].map(k=>`<td class="num">${fmtLap(p[k])}</td>`).join('')}
<td class="num">${p.consistency==null?'—':p.consistency.toFixed(1)+'%'}</td><td class="num">${speed(p.top_speed_kmh)}</td>`;}
const reportColors=['#e65a43','#4988db','#19a56a','#a769dc','#da8e20','#24a9b4','#c8508b','#7a9b2b','#65758d'];
function reportCharts(drivers,cfg){
 const legend=drivers.map((d,i)=>`<span style="color:${reportColors[i%reportColors.length]}">● ${esc(d.nick||d.name)}</span>`).join('');
 const gridPosition=cfg.start_mode==='grid'&&cfg.rank==='position';
 const all=drivers.map(d=>{const passings=(d.passings||[]).filter(p=>!p.origin&&p.lap_ms!=null);return (d.laps||[]).map((_,i)=>{const elapsed=d.laps.slice(0,i+1).reduce((a,b)=>a+b,0);return gridPosition?(passings[i]?.decoder_timecode??elapsed+Number(d.first_decoder_ts||0)):elapsed;});});
 const count=Math.max(1,...all.map(a=>a.length));
 function chart(title,type){
  const series=drivers.map(()=>[]); let ymax=type==='pos'?Math.max(1,drivers.length):1;
  for(let lap=0;lap<count;lap++){
   const order=all.map((a,i)=>({i,t:a[lap]})).filter(v=>v.t!=null).sort((a,b)=>a.t-b.t);
   order.forEach((v,pos)=>{const value=type==='pos'?pos+1:(v.t-order[0].t)/1000;series[v.i].push([lap+1,value]);ymax=Math.max(ymax,value);});
  }
  const y=v=>type==='pos'?40+(v-1)*240/Math.max(1,ymax-1):280-v/ymax*240;
  const grid=Array.from({length:6},(_,i)=>{let v=type==='pos'?1+(ymax-1)*i/5:ymax*i/5;return `<line x1="55" y1="${y(v)}" x2="950" y2="${y(v)}" stroke="#e0e5eb"/><text x="8" y="${y(v)+4}" font-size="11" fill="#64748b">${type==='pos'?'P':''}${v.toFixed(type==='pos'?0:1)}</text>`;}).join('');
  const x=v=>55+(v-1)*895/Math.max(1,count-1);
  const lines=series.map((points,i)=>`<polyline fill="none" stroke="${reportColors[i%reportColors.length]}" stroke-width="2" points="${points.map(p=>`${x(p[0])},${y(p[1])}`).join(' ')}"/>${points.map(p=>`<circle cx="${x(p[0])}" cy="${y(p[1])}" r="3" fill="${reportColors[i%reportColors.length]}"/>`).join('')}`).join('');
  const labels=Array.from({length:count},(_,i)=>i+1).filter(n=>n===1||n===count||n%Math.max(1,Math.ceil(count/12))===0).map(n=>`<text x="${x(n)}" y="305" font-size="11">${n}</text>`).join('');
  return `<section class="report-chart"><h3>${title}</h3><svg viewBox="0 0 980 325">${grid}${lines}${labels}<text x="470" y="322" font-size="11">Lap</text></svg><div class="report-legend">${legend}</div></section>`;
 }
 const pace=drivers.map(d=>(d.laps||[]).slice().sort((a,b)=>a-b));
 const values=pace.flat();const low=Math.min(...values,Infinity),high=Math.max(...values,0);
 let distribution='';
 if(values.length){
  const x=v=>160+(v-low)/Math.max(1,high-low)*760;
  distribution=pace.map((laps,i)=>{if(!laps.length)return '';const n=laps.length,median=(laps[Math.floor((n-1)/2)]+laps[Math.floor(n/2)])/2;
   const y=30+i*32;return `<text x="4" y="${y+4}" font-size="12">${esc(drivers[i].nick||drivers[i].name)}</text><line x1="${x(laps[0])}" y1="${y}" x2="${x(laps[n-1])}" y2="${y}" stroke="#eeb2ac" stroke-width="12"/><line x1="${x(laps[Math.floor((n-1)*.25)])}" y1="${y}" x2="${x(laps[Math.floor((n-1)*.75)])}" y2="${y}" stroke="#dc6357" stroke-width="12"/><line x1="${x(median)}" y1="${y-9}" x2="${x(median)}" y2="${y+9}" stroke="#182b40" stroke-width="2"/>`;}).join('');
  const y=pace.length*32+24;distribution+=Array.from({length:6},(_,i)=>{const v=low+(high-low)*i/5;return `<text x="${x(v)}" y="${y}" font-size="11">${(v/1000).toFixed(2)} s</text>`;}).join('');
 }
 return chart('Position progression','pos')+chart('Gap to leader · seconds','gap')+`<section class="report-chart"><h3>Lap time distribution · medians</h3><svg viewBox="0 0 980 ${Math.max(100,pace.length*32+50)}">${distribution||'<text x="20" y="40">Belum ada lap penuh</text>'}</svg><p class="note">Rentang min–max, kotak kuartil 25–75%, garis median.</p></section>`;
}
let editMemberId=null;
function openMember(id){
 const m=DATA.member.find(x=>x.id===id)||{};editMemberId=m.id||null;
 $('memberTitle').textContent=m.id?'Edit member':'Tambah member';
 [['memberName','name'],['memberNick','nick'],['memberClass','kelas'],['memberCar','mobil']].forEach(([id,k])=>$(id).value=m[k]||'');
 $('memberTts').value=m.tts_name||m.tts||'';
 $('memberTransponders').value=(m.transponder||[]).join(', ');$('modalMember').hidden=false;$('memberName').focus();
}
$('addMember').onclick=()=>openMember();
$('importMembers').onclick=()=>$('memberFile').click();
$('memberFile').onchange=async e=>{const f=e.target.files[0];if(!f)return;try{
 const data=JSON.parse(await f.text());const result=await api('/api/members/import',{members:Array.isArray(data)?data:data.members});
 DATA.member=result.members;renderMember();toast(result.count+' member diimpor secara lokal');
 }catch(err){gagal(err);}finally{e.target.value='';}};
$('memberCancel').onclick=()=>$('modalMember').hidden=true;
$('memberForm').onsubmit=async e=>{
 e.preventDefault();try{
 const res=await api('/api/member',{id:editMemberId,name:$('memberName').value,nick:$('memberNick').value,kelas:$('memberClass').value,mobil:$('memberCar').value,transponder:$('memberTransponders').value,tts_name:$('memberTts').value.trim()});
 DATA.member=res.members;$('modalMember').hidden=true;renderMember();toast('Member tersimpan lokal');
 }catch(err){gagal(err);}
};
$('memberList').addEventListener('click',e=>{const b=e.target.closest('[data-edit-member]');if(b)openMember(b.dataset.editMember);});
$('memberList').addEventListener('click',async e=>{const b=e.target.closest('[data-delete-member]');if(!b)return;const m=DATA.member.find(x=>x.id===b.dataset.deleteMember);if(!m||!confirm(`Hapus driver offline ${m.nick||m.name}?`))return;try{const r=await api('/api/member/delete',{id:m.id});DATA.member=r.members;renderMember();toast('Driver offline dihapus');}catch(err){gagal(err);}});
let reportRaceId=null;
$('sesiPdf').onclick=async()=>{
 if(!reportRaceId)return;
 try{const r=await fetch(BASE+'/api/races/'+encodeURIComponent(reportRaceId)+'/pdf'+(ACCESS_QUERY?'?t='+encodeURIComponent(ACCESS_QUERY):''));if(!r.ok)throw Error('Ekspor PDF gagal');
 const url=URL.createObjectURL(await r.blob());const a=document.createElement('a');a.href=url;a.download='YurLaps-race.pdf';a.click();setTimeout(()=>URL.revokeObjectURL(url),60000);
 }catch(e){gagal(e);}
};

(async function init(){
  try{
    await muatAwal();
    await renderHistory();
    show('practice');
    // Jangan menimpa keadaan sesi: kalau aplikasi dibuka ulang di tengah
    // balapan, timer & label LIVE harus lanjut, bukan kembali ke SIAP.
    const st = sesi ? sesi.status : 'idle';
    const target = sesi && sesi.cfg && sesi.cfg.target === 'time'
      ? sesi.cfg.value * 1000 : 0;
    $('qrClock').textContent = target
      ? fmtClock(Math.max(0, target - (sesi.elapsed_ms || 0)))
      : fmtClock((sesi && sesi.elapsed_ms) || 0);
    renderRaceStatus();
    setInterval(refreshRace,300);
    segarkanDecoder();
    await refreshPractice();
    await refreshAccount();
    setInterval(refreshPractice, 4000);
    setInterval(refreshAccount, 5000);
    setInterval(refreshClub,10000);
  }catch(e){
    toast(e.message || 'Gagal memuat data dari layanan lokal');
    document.body.insertAdjacentHTML('beforeend',
      '<div class="modal"><div class="dlg"><h2>Layanan lokal tidak menjawab</h2>'
      + '<div class="sub">' + esc(e.message || '') + '</div>'
      + '<div class="note">Aplikasi ini memerlukan proses YurLaps di komputer yang sama. '
      + 'Tutup lalu jalankan ulang YurLaps.</div></div></div>');
  }
  setTimeout(()=>{const s=$('startupSplash'); if(s){s.classList.add('hide');setTimeout(()=>s.hidden=true,550);}},700);
})();
})();
