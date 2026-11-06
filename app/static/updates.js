(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const shell = window.yurlapsShell;
  let action = 'check';
  function render(s) {
    const messages = { idle: 'Periksa apakah versi baru tersedia.', checking: 'Memeriksa rilis...',
      current: 'Kamu menggunakan versi terbaru.', available: 'Versi baru tersedia.',
      downloading: `Mengunduh ${s.progress || 0}%`, ready: 'Pembaruan siap dipasang.',
      installing: 'Menyimpan data dan menutup aplikasi...', error: 'Pemeriksaan atau unduhan gagal.',
      unsupported: 'Update tersedia untuk aplikasi Windows terpasang. Untuk mode source, gunakan paket source terbaru.' };
    el('updateVersion').textContent = `Versi sekarang: ${s.current || '-'}${s.latest ? ' / tersedia: ' + s.latest : ''}`;
    el('updateStatus').textContent = s.error || messages[s.state] || messages.idle;
    el('updateNotes').textContent = s.notes || '';
    el('updateProgress').hidden = s.state !== 'downloading';
    el('updateProgress').value = s.progress || 0;
    action = s.state === 'available' ? 'download' : s.state === 'ready' ? 'install' : 'check';
    el('updateAction').textContent = {check: 'Cek pembaruan', download: 'Unduh pembaruan', install: 'Pasang dan mulai ulang'}[action];
    el('updateAction').disabled = ['unsupported', 'checking', 'downloading', 'installing'].includes(s.state);
  }
  el('btnUpdates').onclick = async () => {
    el('modalUpdates').hidden = false;
    try { render(shell?.update ? await shell.update('status') : {state: 'unsupported'}); }
    catch (e) { el('updateStatus').textContent = e.message; }
  };
  el('updateClose').onclick = () => { el('modalUpdates').hidden = true; };
  el('updateAction').onclick = async () => {
    if (action === 'install' && !confirm('Tutup aplikasi, simpan latihan, lalu pasang pembaruan?')) return;
    el('updateAction').disabled = true;
    try { render(await shell.update(action)); }
    catch (e) { el('updateStatus').textContent = e.message; el('updateAction').disabled = false; }
  };
  shell?.onUpdate?.(render);
})();
