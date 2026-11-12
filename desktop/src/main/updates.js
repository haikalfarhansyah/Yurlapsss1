'use strict';

/**
 * Pesan error pembaruan harus ramah untuk operator. Respons mentah dari
 * server (dumps HTTP) tidak pernah ditampilkan; hanya ringkasannya.
 */
function friendlyError(error) {
  const raw = String((error && error.message) || error || '');
  const missing = /\b404\b|NoSuchKey|Object not found|not found/i.test(raw);
  if (missing) return 'Belum ada versi baru dipublikasikan. Periksa kembali nanti.';
  const offline = /ENOTFOUND|ECONNREFUSED|EAI_AGAIN|ETIMEDOUT|network|socket hang up/i.test(raw);
  if (offline) return 'Tidak dapat terhubung ke server pembaruan. Periksa koneksi internet.';
  return 'Pemeriksaan pembaruan gagal. Periksa koneksi internet lalu coba lagi.';
}

function configureUpdates({ app, ipcMain, autoUpdater, getWindow, prepare, stopEngine, cancel }) {
  const supported = app.isPackaged && process.platform === 'win32';
  let busy = false;
  let state = { current: app.getVersion(), state: supported ? 'idle' : 'unsupported', progress: 0 };
  const publish = (values) => {
    state = { ...state, ...values };
    const win = getWindow();
    if (win && !win.isDestroyed()) win.webContents.send('yurlaps:update-state', state);
  };
  autoUpdater.autoDownload = false;
  autoUpdater.autoInstallOnAppQuit = false;
  autoUpdater.allowDowngrade = false;
  autoUpdater.allowPrerelease = false;
  autoUpdater.on('update-available', info => publish({ state: 'available', latest: info.version,
    notes: typeof info.releaseNotes === 'string' ? info.releaseNotes : '', error: '' }));
  autoUpdater.on('update-not-available', () => publish({ state: 'current', error: '' }));
  autoUpdater.on('download-progress', p => publish({ state: 'downloading', progress: Math.round(p.percent) }));
  autoUpdater.on('update-downloaded', () => publish({ state: 'ready', progress: 100, error: '' }));
  autoUpdater.on('error', error => {
    console.error('[yurlaps:update]', error);
    publish({ state: 'error', error: friendlyError(error) });
  });

  ipcMain.handle('yurlaps:update', async (event, action) => {
    const win = getWindow();
    if (!win || event.sender !== win.webContents || event.senderFrame !== win.webContents.mainFrame) {
      throw new Error('Permintaan pembaruan tidak diizinkan');
    }
    if (action === 'status' || !supported) return state;
    if (busy) return state;
    busy = true;
    try {
      if (action === 'check' && state.state !== 'ready') {
        publish({ state: 'checking', error: '' });
        await autoUpdater.checkForUpdates();
      } else if (action === 'download' && state.state === 'available') {
        publish({ state: 'downloading', error: '', progress: 0 });
        await autoUpdater.downloadUpdate();
      } else if (action === 'install' && state.state === 'ready') {
        // Backend locks race start before the engine flushes SQLite and exits.
        await prepare();
        publish({ state: 'installing', error: '' });
        await stopEngine();
        autoUpdater.quitAndInstall(false, true);
      }
    } catch (error) {
      if (action === 'install') {
        // Pesan dari backend sendiri (mis. "Simpan race sebelum memasang").
        await cancel().catch(() => {});
        publish({ state: 'ready', error: error.message });
      } else publish({ state: 'error', error: friendlyError(error) });
    } finally { busy = false; }
    return state;
  });
}

module.exports = { configureUpdates };
