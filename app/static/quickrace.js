/* ============================================================================
   YurLaps — Quick Race (aplikasi nyata, sepenuhnya offline)
   Data club disimpan di SQLite lewat API lokal /api/*. Tidak ada localStorage
   untuk data club, tidak ada permintaan ke internet.
   ========================================================================== */
(function(){
'use strict';

const BASE = (window.__YURLAPS_CFG__ && window.__YURLAPS_CFG__.base) || '';
const $ = id => document.getElementById(id);
const SVG_PLAY = '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5l12 7-12 7z"/></svg>';
const pad = n => String(n).padStart(2,'0');
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

/* ---------- format ---------- */
function fmtLap(ms){
  if(ms == null || !isFinite(ms)) return '—';
  const m = Math.floor(ms / 60000), s = Math.floor(ms / 1000) % 60, r = Math.round(ms % 1000);
  return m ? `${m}:${pad(s)}.${String(r).padStart(3,'0')}` : `${s}.${String(r).padStart(3,'0')}`;
}
function fmtClock(ms){
  ms = Math.max(0, ms || 0);
  const m = Math.floor(ms / 60000), s = Math.floor(ms / 1000) % 60, d = Math.floor((ms % 1000) / 100);
  return `${pad(m)}:${pad(s)}.${d}`;
}
function fmtTgl(ts){
  if(!ts) return '—';
  const d = new Date(ts * 1000 > 1e11 ? ts : ts * 1000);
  return pad(d.getDate()) + ' ' + ['Jan','Feb','Mar','Apr','Mei','Jun','Jul','Agu','Sep','Okt','Nov','Des'][d.getMonth()]
    + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
}
/* km/h = lebar loop (m) ÷ waktu lap (s) × 3,6. Contoh 42 m / 9,412 s = 16 km/h.
   pass_duration (0,1 s) BUKAN waktu satu loop — jangan dipakai di sini. */
function kmh(kelilingM, lapMs){
  if(!kelilingM || !lapMs) return null;
  return Math.round((kelilingM / (lapMs/1000)) * 3.6);
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
  '/api/race/reset','/api/race/save','/api/race/discard','/api/race/upload',
  '/api/track','/api/track/delete','/api/tracks/sync','/api/preset','/api/preset/delete'];
async function api(path, body){
  const opt = { headers: {} };
  if(body !== undefined || RUTE_POST.includes(path)){
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
  return sesi && (sesi.status === 'live' || sesi.status === 'countdown');
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
    tp:p.transponder == null ? '' : String(p.transponder), no:p.no || i+1, luar:!!p.luar
  }));
}

async function segarkanSesi(){
  const r = await api('/api/race/state');
  sesi = r.race;
  return sesi;
}

/* ============================================================================
   NAVIGASI
   ========================================================================== */
const VIEWS = ['quickrace','history','member','track','decoder'];
function show(v){
  VIEWS.forEach(k => { const s = $('view-' + k); if(s) s.style.display = (k === v) ? '' : 'none'; });
  document.querySelectorAll('.tab').forEach(b => b.classList.toggle('on', b.dataset.view === v));
  window.scrollTo(0,0);
  if(v === 'history') renderHistory();
  if(v === 'member') renderMember();
  if(v === 'track') renderTrackPage();
  if(v === 'decoder'){ segarkanDecoder(); renderDecoderLog(); }
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
  sel.disabled = sedangJalan();
  const t = trackAktif();
  if(!t){ $('topTrack').textContent = '—'; return; }
  $('topTrack').textContent = t.name;
  $('footTrack').textContent = t.name;
  const nSesi = t.n_race || 0;
  $('trackMeta').innerHTML =
    `<b>${nSesi} sesi di track ini</b><span>Rekor: ${fmtLap(t.best_ms)} · ${esc(t.holder || '—')}`
    + (t.width_m ? ` · lebar loop ${t.width_m} m` : ' · lebar loop belum diisi') + `</span>`;
  $('trackSrc').textContent = t.synced ? 'dari website' : 'lokal · belum sinkron';
}
function renderPresetSelect(){
  const sel = $('selPreset');
  sel.innerHTML = DATA.preset.map(p =>
    `<option value="${p.id}">${esc(p.name)}</option>`).join('');
  sel.value = state.presetId || '';
}
function renderChips(){
  const c = (sesi && sesi.cfg) || {};
  const target = c.target === 'time'
    ? `waktu ${mmss(c.value)}`
    : `${c.value} lap`;
  const rank = ({lapsTime:'Lap → total waktu', best:'Best lap', top3:'Rata² 3 lap tercepat',
    consecutive:'3 lap berurutan'})[c.rank] || c.rank;
  const mulai = c.start_mode === 'grid' ? 'grid bersamaan' : 'bergiliran';
  const chips = [
    ['Target', target], ['Urutan', rank], ['Mulai', mulai],
    ['Hitung mundur', c.cd + ' detik'], ['Timing', 'YurLaps Decoder']
  ];
  let html = chips.map(([k,v]) => `<span class="chip">${k}: <b>${esc(v)}</b></span>`).join('');
  const t = trackAktif();
  if(t && !t.width_m) html += `<span class="chip warn">Lebar loop belum diisi — km/h tidak dihitung</span>`;
  if(!state.peserta.length) html += `<span class="chip warn">Belum ada peserta</span>`;
  $('chips').innerHTML = html;
  $('qrTitle').textContent = (c.nama || 'Quick Race') + (t ? ' · ' + t.name : '');
}

/* ============================================================================
   RENDER: PAPAN SKOR (dari server)
   ========================================================================== */
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
  const terbaik = Math.min(...baris.filter(p => p.best_ms)
    .map(p => p.best_ms).concat([Infinity]));
  const live = sesi.status === 'live';

  body.innerHTML = baris.map((p, i) => {
    const v = p.kmh;
    const st = live
      ? (p.best_ms != null && p.best_ms === terbaik ? '<span class="st-live">tercepat</span>'
         : '<span class="st-run">jalan</span>')
      : (p.n_lap ? '<span class="st-run">selesai</span>' : '<span class="st-out">belum lintas</span>');
    const luar = p.luar ? '<span class="outside">bukan member</span>' : '';
    return `<tr class="row ${sesi.pilihan === p.uid ? 'sel' : ''}" data-uid="${p.uid}">
      <td class="pos ${i === 0 ? 'first' : ''}">${i+1}</td>
      <td class="nm">${esc(p.nick || p.name)}${luar}<small>${esc(p.name)}${p.transponder == null ? ' · transponder belum ada' : ' · TP ' + p.transponder}</small></td>
      <td class="num r">${p.n_lap}</td>
      <td class="num r">${fmtLap(p.terakhir_ms)}</td>
      <td class="best r">${fmtLap(p.best_ms)}</td>
      <td class="num r">${fmtLap(p.rata_ms)}</td>
      <td class="sig r">${p.sigma_ms == null ? '—' : (p.sigma_ms/1000).toFixed(3)}</td>
      <td class="spd r">${v == null ? '—' : '<b>' + v + '</b>'}</td>
      <td class="r">${st}</td></tr>`;
  }).join('');

  body.querySelectorAll('tr.row').forEach(tr => {
    tr.onclick = () => { if(sesi) sesi.pilihan = tr.dataset.uid; renderBoard(); renderDetail(); };
  });
}

function renderDetail(){
  const p = ((sesi && sesi.klasemen) || []).find(x => x.uid === sesi.pilihan)
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
  $('dBest').textContent = fmtLap(p.best_ms);
  $('dAvg').textContent = fmtLap(p.rata_ms);
  $('dSig').textContent = p.sigma_ms == null ? '—' : (p.sigma_ms/1000).toFixed(3);
  const lap = p.laps || [];
  $('dLapList').innerHTML = lap.slice().reverse().map((ms,i) => {
    const n = lap.length - i;
    const best = ms === Math.min(...lap);
    return `<div class="laprow"><span class="n">L${n}</span>
      <span class="t ${best ? 'best' : ''}">${fmtLap(ms)}</span></div>`;
  }).join('') || '<div class="note" style="font-size:11px">Belum ada lap.</div>';
  // grafik sederhana: batang per lap
  const maks = Math.max(...lap, 1);
  $('dGraph').innerHTML = lap.map(ms =>
    `<i style="height:${Math.max(8, Math.round((ms/maks)*100))}%"></i>`).join('');
}

function renderLog(){
  const log = (sesi && sesi.log) || [];
  $('pLogN').textContent = log.length + ' terakhir';
  // kelas .prow / .skip sudah ada di CSS (kisi rapi + warna); jangan bikin
  // kelas baru yang tidak punya gaya.
  $('pLog').innerHTML = log.map(e =>
    `<div class="prow${e.skip ? ' skip' : ''}">
      <span class="tm">${esc(e.jam)}</span>
      <span class="nm" title="${e.skip
        ? 'Transponder ini tidak terdaftar sebagai peserta'
        : 'Lintasan sah untuk peserta ini'}">${esc(e.nama)}</span>
      <span class="rs">${e.rssi == null ? '—' : Number(e.rssi).toFixed(0)}</span>
      <span class="lp">${e.skip ? 'luar' : 'L' + e.lap}</span>
    </div>`).join('')
    || '<div class="note">Belum ada lintasan. Tekan Start, lalu pembalap melewati garis.</div>';
}

/* ============================================================================
   COUNTDOWN + START + FINISH
   ========================================================================== */
function bunyi(freq, dur){
  if(!sesi || !sesi.cfg || !sesi.cfg.sound) return;
  try{
    const AC = window.AudioContext || window.webkitAudioContext;
    if(!AC) return;
    if(!bunyi.ctx) bunyi.ctx = new AC();
    const ctx = bunyi.ctx;
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.type = 'square'; o.frequency.value = freq;
    g.gain.value = .05;
    o.connect(g); g.connect(ctx.destination);
    o.start(); o.stop(ctx.currentTime + dur);
  }catch(e){}
}

let cdTimer = null;
async function mulaiCountdown(){
  if(!state.peserta.length){ toast('Pilih peserta dulu'); openPeserta(); return; }
  if(sedangJalan()){ toast('Sesi sedang berjalan'); return; }
  try{
    await api('/api/race/start');
    sesi = await segarkanSesi();
  }catch(e){ return gagal(e); }

  const n = Number((sesi.cfg && sesi.cfg.cd) || 5);
  let sisa = n;
  const layer = $('cdLayer');
  layer.hidden = false;
  document.body.classList.add('busy');
  $('cdWho').textContent = `${(trackAktif() || {}).name || '—'} · ${sesi.peserta.length} pembalap`;
  clearInterval(cdTimer);
  const langkah = () => {
    $('cdNum').textContent = sisa;
    $('cdNum').className = 'n';
    $('cdCap').textContent = sisa === n ? 'Bersiap' : 'Menuju garis start';
    bunyi(660, .18);
    if(sisa === 0){
      $('cdNum').textContent = 'GO';
      $('cdNum').className = 'n go';
      $('cdCap').textContent = 'Balapan dimulai';
      bunyi(1180, .4);
      clearInterval(cdTimer);
      setTimeout(() => { layer.hidden = true; document.body.classList.remove('busy'); mulaiLive(); }, 700);
      return;
    }
    sisa--;
  };
  langkah();
  cdTimer = setInterval(langkah, 1000);
  renderAll();
}

async function mulaiLive(){
  try{
    await api('/api/race/go');
    sesi = await segarkanSesi();
  }catch(e){ return gagal(e); }
  $('liveTag').className = 'live';
  $('liveTag').innerHTML = '<i></i>LIVE';
  $('asideDec').textContent = 'aktif · sesi berjalan';
  renderAll();
  mulaiTick();
  toast('Quick Race dimulai');
}

let tickTimer = null;
function mulaiTick(){
  clearInterval(tickTimer);
  tickTimer = setInterval(async () => {
    if(!sesi) return;
    if(sesi.status !== 'live'){ clearInterval(tickTimer); return; }
    try{ await segarkanSesi(); }catch(e){ return; }
    const target = sesi.cfg.target === 'time' ? sesi.cfg.value * 1000 : 0;
    const el2 = sesi.elapsed_ms || 0;
    $('qrClock').textContent = target ? fmtClock(Math.max(0, target - el2)) : fmtClock(el2);
    // log ikut disegarkan: lintasan bisa datang dari decoder, bukan hanya
    // dari tombol di layar ini (mis. saat diuji lewat API)
    renderBoard(); renderDetail(); renderLog();
    if(sesi.status === 'done'){ clearInterval(tickTimer); selesaiTampil(); }
  }, 500);
}

async function catatLintasan(){
  if(!sesi || sesi.status !== 'live'){ toast('Mulai sesi dulu'); return; }
  const peserta = (sesi.peserta || []).filter(p => p.transponder != null);
  if(!peserta.length){ toast('Peserta belum punya transponder'); return; }
  const hidup = peserta.filter(p => !p.tunggu);
  const p = (hidup.length ? hidup : peserta)[Math.floor(Math.random() * (hidup.length || peserta.length))];
  try{
    const r = await api('/api/race/pass', {
      transponder: p.transponder,
      rssi: -45 - Math.random() * 20,
      dur_us: 90000 + Math.round(Math.random() * 50000),
      simulasi: 1   // tombol uji: server membuat waktu lap yang wajar
    });
    sesi = r.race;
    renderAll();
    if(r.hasil && r.hasil.diterima){
      toast('Lintasan dicatat: ' + (p.nick || p.name) + ' · L' + r.hasil.lap);
    }
    if(sesi.status === 'done'){ selesaiTampil(); }
  }catch(e){ gagal(e); }
}

let simTimer = null;
function jadwalkanSim(){
  clearTimeout(simTimer);
  const putar = () => {
    if(!sesi || sesi.status !== 'live') return;
    catatLintasan();
    simTimer = setTimeout(putar, 900 + Math.random() * 1400);
  };
  simTimer = setTimeout(putar, 1200);
}

async function selesai(alasan){
  if(!sesi || sesi.status !== 'live') return;
  try{
    await api('/api/race/finish', { alasan: alasan || 'dihentikan operator' });
    sesi = await segarkanSesi();
  }catch(e){ return gagal(e); }
  clearInterval(tickTimer); clearTimeout(simTimer); clearInterval(cdTimer);
  selesaiTampil();
}

function selesaiTampil(){
  clearInterval(tickTimer); clearTimeout(simTimer);
  $('liveTag').className = 'live idle';
  $('liveTag').innerHTML = '<i></i>SELESAI';
  $('asideDec').textContent = 'aktif · menunggu lintasan';
  renderAll();
  toast('Sesi selesai · ' + (sesi.alasan_selesai || ''));
  bukaSave();
}

async function resetSesi(diam){
  if(sesi && sesi.status === 'live'
     && !confirm('Sesi sedang berjalan. Reset akan menghapus lap yang sudah tercatat. Lanjut?')) return;
  try{
    const r = await api('/api/race/reset');
    sesi = r.race;
  }catch(e){ return gagal(e); }
  clearInterval(tickTimer); clearTimeout(simTimer); clearInterval(cdTimer);
  $('qrClock').textContent = '00:00.0';
  $('liveTag').className = 'live idle';
  $('liveTag').innerHTML = '<i></i>SIAP';
  renderAll();
  if(!diam) toast('Sesi direset');
}

function renderTombol(){
  const st = sesi ? sesi.status : 'idle';
  const jalan = (st === 'live' || st === 'countdown');
  $('btnStart').disabled  = jalan;
  $('btnStart').innerHTML = st === 'done' ? SVG_PLAY + ' Start lagi' : SVG_PLAY + ' Start';
  $('btnFinish').disabled = st !== 'live';
  // Peserta hanya dikunci saat sesi benar-benar berjalan. Saat 'done',
  // peserta dikunci juga sampai sesi disimpan/dibuang -- server akan menolak
  // kalau dipaksa, jadi tombolnya dinonaktifkan daripada gagal diam-diam.
  $('btnPeserta').disabled = jalan || st === 'done';
  $('btnSimPass').disabled = st !== 'live';
  $('selTrack').disabled = jalan;
  $('qrLead').textContent = st === 'live'
    ? 'Sesi berjalan — lap tercatat lewat decoder.'
    : st === 'countdown' ? 'Hitung mundur berjalan…'
    : st === 'done' ? 'Sesi selesai. Simpan ke History Race atau reset.'
    : state.peserta.length
      ? 'Siap. Tekan Start untuk hitung mundur ' + ((sesi.cfg && sesi.cfg.cd) || 5) + ' detik.'
      : 'Pilih peserta dulu (tombol Peserta), lalu Start.';
}

/* ============================================================================
   PESERTA
   ========================================================================== */
function openPeserta(){
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
      <input type="checkbox" ${on ? 'checked' : ''} data-chk>
      <span class="nm"><b>${esc(m.name)}</b><small>${sub}${tps.length > 1
        ? ' · ' + tps.length + ' transponder' : ''}</small></span>
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
$('rCancel').onclick = () => { $('modalPeserta').hidden = true; };
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
   PENGATURAN SESI
   ========================================================================== */
function bukaSetting(){
  const c = (sesi && sesi.cfg) || {};
  $('setName').value = c.nama || 'Quick Race';
  pilihChip('setTarget', c.target);
  isiNilai(c.target, c.value);
  $('setRank').value = c.rank || 'lapsTime';
  $('setStart').value = c.start_mode || 'grid';
  $('setCd').value = c.cd || 5;
  pilihChip('setSound', String(c.sound ? 1 : 0));
  $('modalSetting').hidden = false;
  // Membuka pengaturan saja bukan perubahan: label hanya berubah kalau ada
  // isian yang benar-benar disentuh (tandaiDirty dipanggil dari oninput).
  const p = presetAktif();
  const sama = p && p.name === c.nama && p.target === c.target && p.value === c.value
    && p.rank === c.rank && p.start_mode === c.start_mode && p.cd === c.cd;
  $('presetState').textContent = sama ? 'tersimpan' : 'diubah · belum diterapkan';
  $('presetState').className = sama ? 'dirty ok' : 'dirty';
  dirty = !sama;
}
function isiNilai(target, nilai){
  $('setValue').innerHTML = target === 'time'
    ? [[180,'3 menit'],[300,'5 menit'],[600,'10 menit'],[900,'15 menit']]
      .map(([v,t]) => `<option value="${v}">${t}</option>`).join('')
    : [[5,'5 lap'],[10,'10 lap'],[20,'20 lap'],[30,'30 lap']]
      .map(([v,t]) => `<option value="${v}">${t}</option>`).join('');
  $('setValue').value = nilai;
  $('setTargetLbl').textContent = target === 'time' ? 'Durasi' : 'Jumlah lap';
}
function pilihChip(wrapId, v){
  document.querySelectorAll('#' + wrapId + ' button').forEach(b =>
    b.classList.toggle('on', b.dataset.v === String(v)));
}
document.querySelectorAll('#setTarget button').forEach(b => b.onclick = () => {
  pilihChip('setTarget', b.dataset.v);
  isiNilai(b.dataset.v, b.dataset.v === 'time' ? 180 : 10);
  tandaiDirty();
});
document.querySelectorAll('#setSound button').forEach(b => b.onclick = () => {
  pilihChip('setSound', b.dataset.v); tandaiDirty();
});
['setName','setValue','setRank','setStart','setCd'].forEach(id => $(id).oninput = tandaiDirty);

function tandaiDirty(){
  dirty = true;
  $('presetState').textContent = 'diubah · belum diterapkan';
  $('presetState').className = 'dirty';
}
function tandaiSimpan(){
  dirty = false;
  $('presetState').textContent = 'tersimpan';
  $('presetState').className = 'dirty ok';
}
$('setCancel').onclick = () => { $('modalSetting').hidden = true; };
$('setSave').onclick = async () => {
  if(sedangJalan()){ toast('Pengaturan tidak bisa diubah saat sesi berjalan'); return; }
  const body = {
    name: $('setName').value.trim() || 'Quick Race',
    target: document.querySelector('#setTarget button.on').dataset.v,
    value: Number($('setValue').value),
    rank: $('setRank').value,
    start_mode: $('setStart').value,
    cd: Number($('setCd').value),
    sound: Number(document.querySelector('#setSound button.on').dataset.v)
  };
  try{
    await api('/api/race/cfg', body);
    sesi = await segarkanSesi();
  }catch(e){ return gagal(e); }
  $('modalSetting').hidden = true;
  renderAll();
  toast('Pengaturan diterapkan');
};

/* ============================================================================
   PRESET
   ========================================================================== */
$('gearBtn').onclick = e => {
  e.stopPropagation();
  const m = $('gearMenu');
  if(m.hasAttribute('hidden')) m.removeAttribute('hidden'); else m.setAttribute('hidden','');
};
document.addEventListener('click', e => {
  const m = $('gearMenu');
  if(!m.hasAttribute('hidden') && !m.contains(e.target) && e.target !== $('gearBtn'))
    m.setAttribute('hidden','');
});
$('btnApply').onclick = async () => {
  const p = presetAktif();
  if(!p) return;
  try{
    await api('/api/race/cfg', { name:p.name, target:p.target, value:p.value,
      rank:p.rank, start_mode:p.start_mode, cd:p.cd, sound:p.sound });
    sesi = await segarkanSesi();
  }catch(e){ return gagal(e); }
  renderAll(); tandaiSimpan();
  toast('Preset diterapkan: ' + p.name);
};
$('selPreset').onchange = () => {
  state.presetId = $('selPreset').value;
  const p = presetAktif();
  $('presetName').textContent = p ? p.name : '—';
  tandaiDirty();
};
$('btnSaveAs').onclick = async () => {
  const nama = prompt('Nama preset baru:', ((sesi.cfg.nama || 'Quick Race') + ' (salinan)'));
  if(!nama) return;
  try{
    const r = await api('/api/preset', { name:nama, target:sesi.cfg.target,
      value:sesi.cfg.value, rank:sesi.cfg.rank, start_mode:sesi.cfg.start_mode,
      cd:sesi.cfg.cd, sound:sesi.cfg.sound });
    DATA.preset = r.presets;
    state.presetId = r.preset.id;
  }catch(e){ return gagal(e); }
  renderPresetSelect(); $('presetName').textContent = nama; tandaiSimpan();
  toast('Preset disimpan: ' + nama);
};
$('mEdit').onclick = () => { $('gearMenu').setAttribute('hidden',''); bukaSetting(); };
$('mDup').onclick = async () => {
  $('gearMenu').setAttribute('hidden','');
  const p = presetAktif();
  if(!p) return;
  try{
    const r = await api('/api/preset', { name:p.name + ' (salinan)', target:p.target,
      value:p.value, rank:p.rank, start_mode:p.start_mode, cd:p.cd, sound:p.sound });
    DATA.preset = r.presets; state.presetId = r.preset.id;
  }catch(e){ return gagal(e); }
  renderPresetSelect();
  toast('Preset diduplikat');
};
$('mNew').onclick = () => { $('gearMenu').setAttribute('hidden',''); bukaSetting(); };
$('mDel').onclick = async () => {
  $('gearMenu').setAttribute('hidden','');
  if(DATA.preset.length <= 1){ toast('Minimal satu preset harus ada'); return; }
  const p = presetAktif();
  if(!p) return;
  if(!confirm('Hapus preset "' + p.name + '"? Sesi yang sudah tersimpan tidak berubah.')) return;
  try{
    const r = await api('/api/preset/delete', { id:p.id });
    DATA.preset = r.presets; state.presetId = (DATA.preset[0] || {}).id;
  }catch(e){ return gagal(e); }
  renderPresetSelect(); renderAll();
  toast('Preset dihapus');
};

/* ============================================================================
   TRACK
   ========================================================================== */
function renderTrackPage(){
  const wrap = $('trackWrap');
  wrap.innerHTML = DATA.track.map(t => `
    <div class="trackrow" data-id="${t.id}">
      <span class="ico"><svg viewBox="0 0 24 24"><path d="M5 5c-4 0-4 14 0 14h4c4 0 2-6 6-6h3c5 0 5-8 0-8z"/></svg></span>
      <span class="who"><b>${esc(t.name)} ${t.synced
        ? '<span class="badge-sync ya">tersinkron</span>'
        : '<span class="badge-sync belum">belum sinkron</span>'}</b>
        <span>${esc(t.addr || '—')}${t.width_m ? ' · lebar loop ' + t.width_m + ' m' : ' · lebar loop belum diisi'}</span></span>
      <span class="rec"><b>${fmtLap(t.best_ms)}</b>${esc(t.holder || '—')}</span>
      <span class="act">
        <button class="btn sm" data-act="edit">Edit</button>
        <button class="btn sm icon danger" data-act="del" title="Hapus">×</button>
      </span>
    </div>`).join('') || '<div class="empty"><b>Belum ada track</b>Tekan “Tambah track”.</div>';

  wrap.querySelectorAll('.trackrow').forEach(row => {
    const id = row.dataset.id;
    row.querySelectorAll('button').forEach(b => b.onclick = async () => {
      if(b.dataset.act === 'edit') return bukaTrack(id);
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
  editTrackId = id || null;
  const t = id ? DATA.track.find(x => x.id === id) : null;
  $('trkTitle').textContent = t ? 'Edit track' : 'Tambah track';
  $('trkName').value = t ? t.name : '';
  $('trkAddr').value = t ? (t.addr || '') : '';
  $('trkWidth').value = t ? (t.width_m || '') : '';
  $('trkNote').value = t ? (t.note || '') : '';
  hitungTrack();
  $('modalTrack').hidden = false;
}
function hitungTrack(){
  const w = Number($('trkWidth').value);
  $('trkHitung').innerHTML = w
    ? `Lebar loop <b>${w} m</b> — contoh kecepatan rata-rata satu lap: `
      + `lap 9.4 s → <b>${kmh(w,9400)} km/h</b>; lap 12.1 s → <b>${kmh(w,12100)} km/h</b>`
    : 'Isi lebar loop untuk melihat contoh kecepatan.';
}
$('trkWidth').oninput = hitungTrack;
$('trkCancel').onclick = () => { $('modalTrack').hidden = true; };
$('trkSave').onclick = async () => {
  const name = $('trkName').value.trim();
  if(!name){ toast('Nama track belum diisi'); $('trkName').focus(); return; }
  const body = { name, addr: $('trkAddr').value.trim(),
    width_m: Number($('trkWidth').value) || 0, note: $('trkNote').value.trim() };
  if(editTrackId) body.id = editTrackId;
  try{
    const r = await api('/api/track', body);
    DATA.track = r.tracks;
    if(editTrackId === state.trackId || !editTrackId){
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
      <span class="avatar">${esc((m.nick || m.name).slice(0,2).toUpperCase())}</span>
      <span class="who"><b>${esc(m.nick || m.name)}</b><span>${esc(m.name)}</span></span>
      <span class="tps">${(m.transponder||[]).length
        ? m.transponder.map(t => `<span class="tp">${t}</span>`).join('')
        : '<span style="font-size:10px">transponder belum dipetakan</span>'}<br>${
        esc([m.mobil, m.kelas].filter(Boolean).join(' · ') || 'mobil & kelas belum diisi')}</span>
      <span class="role ${m.role}">${esc((m.role || 'member').replace('_','-'))}</span>
      <span class="st-p ${m.status}">${m.status === 'active' ? 'aktif' : 'menunggu'}</span>
    </div>`).join('') || '<div class="empty">Tidak ada member yang cocok.</div>';
  $('memberCount').textContent = list.length + ' tampil';
  $('mActive').textContent  = DATA.member.filter(m => m.status === 'active').length;
  $('mPending').textContent = DATA.member.filter(m => m.status === 'pending').length;
  $('mTp').textContent = DATA.member.reduce((s,m) => s + (m.transponder||[]).length, 0);
  $('mTrack').textContent = DATA.track.length;
}
$('memberSearch').oninput = renderMember;

const decLog = [];
function catatDecoder(e, v){
  decLog.unshift({ tp:e.transponder, nama:e.nama, rssi:e.rssi, durUs:e.dur_us,
    kmh:v, jam:e.jam, t:Date.now() });
  del(decLog, 40);
  renderDecoderLog();
}
function del(a, n){ if(a.length > n) a.length = n; }
function renderDecoderLog(){
  const body = $('decLogBody');
  $('decLogN').textContent = decLog.length + ' terakhir';
  body.innerHTML = decLog.slice(0, 20).map(e => `
    <tr><td class="mono">${e.tp}</td><td class="nm">${esc(e.nama)}</td>
      <td class="num r">${e.rssi == null ? '—' : Number(e.rssi).toFixed(1)}</td>
      <td class="num r">${e.durUs == null ? '—' : (e.durUs/1e6).toFixed(3) + ' s'}</td>
      <td class="spd r">${e.kmh == null ? '—' : '<b>'+e.kmh+'</b>'}</td>
      <td class="num r">${esc(e.jam)}</td></tr>`).join('')
    || '<tr><td colspan="6"><div class="note">Belum ada lintasan.</div></td></tr>';
  const perMenit = decLog.length
    ? Math.round(decLog.length / Math.max(1, (Date.now() - decLog[0].t)/60000)) : 0;
  $('decRate').textContent = perMenit;
}
/* ---------------------------------------------------------------- Decoder
   Nilai di halaman ini dibaca dari keadaan mesin sungguhan (/api/state),
   bukan tulisan tetap. Kalau decoder belum terpasang, UI mengatakannya. */
let decOnline = false;

async function segarkanDecoder(){
  let st = null;
  try{
    const r = await fetch(BASE + '/api/state');
    if(r.ok) st = await r.json();
  }catch(e){ return; }
  if(!st) return;
  const d = st.decoder || {};
  decOnline = !!d.online;
  $('decEndpoint').textContent = d.endpoint || '—';
  const g = d.gain == null ? 20 : Number(d.gain);
  $('decGain').textContent = g.toFixed(2);
  gainTersimpan = g;
  if(document.activeElement !== $('gainRange')){
    $('gainRange').value = g;
    $('gainVal').textContent = g.toFixed(1) + ' dB';
    $('gainStatus').textContent = 'tersimpan';
  }
  $('decDev').textContent = d.name || 'YurLaps Decoder';
  const led = $('decLed');
  if(decOnline){
    led.innerHTML = '<i></i>aktif';
    led.style.color = '';
    led.parentElement && (led.parentElement.style.fontSize = '13px');
  } else {
    led.innerHTML = '<i style="background:#a9b5c3;box-shadow:none"></i>menunggu decoder';
    led.style.color = 'var(--muted)';
  }
  if(!decLog.length) $('decRate').textContent = '0';
  if(!sesi || sesi.status !== 'live'){
    $('asideDec').textContent = decOnline
      ? 'aktif · menunggu lintasan' : 'menunggu decoder';
  }
}

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

$('btnDecStop').onclick = () => {
  // Hentikan pengelolaan decoder oleh YurLaps (perangkat sungguhan).
  api('/api/decoder/restart', {}).catch(() => {});
  toast(decOnline
    ? 'YurLaps berhenti mengelola decoder. Nyalakan lagi dengan “Mulai ulang”.'
    : 'Belum ada decoder terpasang untuk dihentikan.');
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
  $('histBadge').textContent = list.length;

  const q = $('histFilter').value || 'semua';
  const tampil = list.filter(r2 => q === 'semua' || r2.track_id === q);
  $('histWrap').style.display = tampil.length ? 'none' : '';
  $('hist').style.display = tampil.length ? '' : 'none';
  $('histCount').textContent = tampil.length + ' sesi';

  const tb = $('histBody');
  tb.innerHTML = tampil.map(r2 => {
    const badge = r2.sync === 'terkirim'
      ? '<span class="st-live">terkirim</span>'
      : '<span class="st-out">belum</span>';
    const durasi = (r2.ended_at && r2.started_at)
      ? Math.max(0, Math.round((r2.ended_at - r2.started_at) * 1000)) : 0;
    return `<tr class="row" data-id="${r2.id}">
      <td class="nm">${esc(r2.name)}<small>${fmtTgl(r2.started_at)}</small></td>
      <td>${esc(r2.track_name || '—')}</td>
      <td class="num r">${r2.n_driver || 0}</td>
      <td class="num r">${r2.n_lap || 0}</td>
      <td class="best r">${fmtLap(r2.best)}</td>
      <td class="num r">${fmtClock(durasi)}</td>
      <td class="r">${badge}</td>
      <td class="r"><button class="btn sm" data-act="kirim">Unggah</button></td>
    </tr>`;
  }).join('');

  tb.querySelectorAll('tr.row').forEach(tr => {
    tr.onclick = e => {
      if(e.target.dataset.act === 'kirim') return unggah(tr.dataset.id);
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

async function bukaSesi(id){
  let r;
  try{
    const res = await api('/api/races/' + encodeURIComponent(id));
    r = res.race;
  }catch(e){ return gagal(e); }
  $('sesiTitle').textContent = r.name;
  const durasi = (r.ended_at && r.started_at)
    ? Math.max(0, Math.round((r.ended_at - r.started_at) * 1000)) : 0;
  $('sesiSub').textContent = `${r.track_name || '—'} · ${fmtTgl(r.started_at)} · durasi ${fmtClock(durasi)}`
    + ` · ${r.drivers.length} pembalap · ${r.drivers.reduce((s,d) => s + d.laps.length, 0)} lap`
    + ` · ${r.sync === 'terkirim' ? 'terkirim' : 'belum diunggah'}`;
  $('sesiBody').innerHTML = `<table><thead><tr><th>#</th><th>Pembalap</th>
      <th class="r">Lap</th><th class="r">Best</th><th class="r">km/h</th>
      <th class="r">Transponder</th><th>Semua lap</th></tr></thead><tbody>`
    + r.drivers.map(d => `<tr>
        <td class="pos">${d.pos}</td>
        <td class="nm">${esc(d.nick || d.name)}<small>${esc(d.name)}</small></td>
        <td class="num r">${d.laps.length}</td>
        <td class="best r">${fmtLap(d.best_ms)}</td>
        <td class="spd r">${d.kmh == null ? '—' : '<b>'+d.kmh+'</b>'}</td>
        <td class="mono r">${d.transponder == null ? '—' : d.transponder}</td>
        <td class="mono" style="font-size:11px">${d.laps.map(fmtLap).join(' · ') || '—'}</td>
      </tr>`).join('') + '</tbody></table>';
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
  await resetSesi(true);
  toast('Sesi dibuang');
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
  await resetSesi(true);
  await muatUlangData();
  await renderHistory();
  show('history');
};

/* ============================================================================
   AKSI TOMBOL QUICK RACE
   ========================================================================== */
$('btnPeserta').onclick = openPeserta;
$('btnSetting').onclick = bukaSetting;
$('btnReset').onclick = () => resetSesi(false);
$('btnStart').onclick = mulaiCountdown;
$('btnFinish').onclick = () => selesai('dihentikan operator');
$('btnClearLog').onclick = () => { toast('Passing log ada di sisi sesi'); };
$('btnSimPass').onclick = () => catatLintasan();
$('selTrack').onchange = async () => {
  const id = $('selTrack').value;
  if(sedangJalan()){ $('selTrack').value = state.trackId; toast('Track tidak bisa diganti saat sesi berjalan'); return; }
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
  $('presetName').textContent = (presetAktif() || {}).name || '—';
  $('nPeserta').textContent = state.peserta.length;
  renderBoard(); renderDetail(); renderLog(); renderTombol();
  renderMember(); renderTrackPage();
  $('topClub').textContent = (DATA.club && DATA.club.name) || '—';
  const namaClub = (DATA.club && DATA.club.name) || 'Club';
  $('akunNick').textContent = namaClub;
  $('akunAv').textContent = namaClub.slice(0,2).toUpperCase();
  $('akunRole').textContent = (DATA.club && DATA.club.role ? DATA.club.role : 'leader')
    + ' · ' + ((DATA.club && DATA.club.name) || '—');
  if(sesi){
    const belum = (DATA.ringkasan && DATA.ringkasan.belum_terkirim) || 0;
    $('pilltxt').textContent = 'Offline — ' + belum + ' sesi belum diunggah';
    if(sesi.status === 'live'){
      $('liveTag').className = 'live';
      $('liveTag').innerHTML = '<i></i>LIVE';
    } else if(sesi.status === 'done'){
      $('liveTag').className = 'live idle';
      $('liveTag').innerHTML = '<i></i>SELESAI';
    } else {
      $('liveTag').className = 'live idle';
      $('liveTag').innerHTML = '<i></i>SIAP';
    }
  }
  renderHistFilter();
}
function renderHistFilter(){
  const sel = $('histFilter');
  const lama = sel.value;
  sel.innerHTML = '<option value="semua">Semua track</option>'
    + DATA.track.map(t => `<option value="${t.id}">${esc(t.name)}</option>`).join('');
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

/* ---------------------------------------------------------------------------
   Panel akun: hanya menampilkan keadaan lokal (belum ada login di mode offline)
   ------------------------------------------------------------------------- */
/* Panel akun: jujur menyebut keadaan sebenarnya. Belum ada login di tahap
   offline, jadi jangan menampilkan nama orang seolah sudah masuk. */
$('btnAkun').onclick = () => {
  const club = DATA.club || {};
  const rk = DATA.ringkasan || {};
  alert('Club aktif : ' + (club.name || '—')
    + '\nPeran      : ' + (club.role || 'leader')
    + '\nMode       : offline (data di perangkat ini)'
    + '\nSesi belum diunggah: ' + (rk.belum_terkirim == null ? '—' : rk.belum_terkirim)
    + '\n\nLogin akun dan sinkronisasi ke website belum aktif '
    + 'pada tahap offline ini.');
};

/* ============================================================================
   MULAI
   ========================================================================== */
window.addEventListener('unhandledrejection', ev => {
  console.error('YurLaps: janji gagal', ev.reason && ev.reason.stack || ev.reason);
});

(async function init(){
  try{
    await muatAwal();
    await renderHistory();
    show('quickrace');
    // Jangan menimpa keadaan sesi: kalau aplikasi dibuka ulang di tengah
    // balapan, timer & label LIVE harus lanjut, bukan kembali ke SIAP.
    const st = sesi ? sesi.status : 'idle';
    const target = sesi && sesi.cfg && sesi.cfg.target === 'time'
      ? sesi.cfg.value * 1000 : 0;
    $('qrClock').textContent = target
      ? fmtClock(Math.max(0, target - (sesi.elapsed_ms || 0)))
      : fmtClock((sesi && sesi.elapsed_ms) || 0);
    if(st === 'live') mulaiTick();
    segarkanDecoder();
  }catch(e){
    toast(e.message || 'Gagal memuat data dari layanan lokal');
    document.body.insertAdjacentHTML('beforeend',
      '<div class="modal"><div class="dlg"><h2>Layanan lokal tidak menjawab</h2>'
      + '<div class="sub">' + esc(e.message || '') + '</div>'
      + '<div class="note">Aplikasi ini memerlukan proses YurLaps di komputer yang sama. '
      + 'Tutup lalu jalankan ulang YurLaps.</div></div></div>');
  }
})();
})();
