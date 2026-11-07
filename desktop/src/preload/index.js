'use strict';
/**
 * Preload: jembatan kecil antara UI dan jendela aplikasi.
 *
 * UI YurLaps tetap berupa halaman web biasa (dilayani mesin Python di
 * 127.0.0.1). Preload hanya menambahkan hal-hal yang memang urusan jendela:
 * versi aplikasi, tombol layar penuh untuk TV, dan penanda "mode aplikasi".
 */
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('yurlapsShell', {
  /** true = dibuka di jendela aplikasi (bukan di browser biasa) */
  isAppWindow: true,
  platform: process.platform,
  /** Open the website login in the user's default browser. */
  openExternal: (url) => ipcRenderer.invoke('yurlaps:open-external', String(url || '')),
  update: action => ipcRenderer.invoke('yurlaps:update', action),
  onUpdate: cb => ipcRenderer.on('yurlaps:update-state', (_event, state) => cb(state)),
  /** versi jendela (bukan versi mesin; versi mesin ada di /api/health) */
  version: process.env.npm_package_version || '',
  /** layar penuh untuk TV pit */
  toggleFullscreen: () => ipcRenderer.send('yurlaps:toggle-fullscreen'),
  isFullscreen: () => ipcRenderer.invoke('yurlaps:is-fullscreen'),
  onFullscreenChanged: (cb) => {
    ipcRenderer.on('yurlaps:fullscreen', (_e, value) => cb(value));
  },
});
