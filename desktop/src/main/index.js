'use strict';
/**
 * Proses utama YurLaps (Electron).
 *
 * Tugasnya cuma dua:
 *   1. jalankan mesin Python (`python -m app --electron`) sebagai proses anak,
 *   2. tampilkan UI-nya di jendela aplikasi sendiri.
 *
 * Mesin Python mendengarkan HANYA di 127.0.0.1 dengan port acak, jadi:
 *   - tidak ada dialog firewall Windows,
 *   - UI tidak bisa dibuka dari komputer lain di jaringan,
 *   - tidak ada jendela konsol hitam.
 *
 * Saat dijalankan dari source (belum dibundel), mesin dipanggil lewat
 * interpreter Python yang ditemukan. Saat dibundel (build), mesin dipanggil
 * lewat YurLapsEngine.exe yang disalin electron-builder ke folder resources.
 */
const { app, BrowserWindow, Menu, shell, dialog, ipcMain } = require('electron');
const { spawn } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');

const IS_DEV = process.argv.includes('--dev');
const ROOT = path.join(__dirname, '..', '..');          // .../desktop
const REPO = path.join(ROOT, '..');                     // .../stintlab

/** @type {import('node:child_process').ChildProcess | null} */
let engine = null;
/** @type {BrowserWindow | null} */
let win = null;
let engineUrl = '';

// ---------------------------------------------------------------------------
// 1. Cari mesin Python
// ---------------------------------------------------------------------------
function engineCommand() {
  // (a) hasil build: resources/engine/YurLapsEngine.exe
  const bundled = path.join(process.resourcesPath || '', 'engine', 'YurLapsEngine.exe');
  if (process.platform === 'win32' && fs.existsSync(bundled)) {
    return { cmd: bundled, args: ['--electron'], cwd: path.dirname(bundled) };
  }
  const bundledPosix = path.join(process.resourcesPath || '', 'engine', 'YurLapsEngine');
  if (process.platform !== 'win32' && fs.existsSync(bundledPosix)) {
    return { cmd: bundledPosix, args: ['--electron'], cwd: path.dirname(bundledPosix) };
  }
  // (b) dari source: python -m app --electron, dijalankan dari folder proyek
  const envPy = process.env.YURLAPS_PYTHON;
  const py = envPy || (process.platform === 'win32' ? 'python' : 'python3');
  return { cmd: py, args: ['-m', 'app', '--electron'], cwd: REPO };
}

// ---------------------------------------------------------------------------
// 2. Jalankan mesin, tunggu satu baris JSON {"event":"ready","port":...}
// ---------------------------------------------------------------------------
function startEngine() {
  return new Promise((resolve, reject) => {
    const { cmd, args, cwd } = engineCommand();
    engine = spawn(cmd, args, {
      cwd,
      env: { ...process.env, PYTHONUNBUFFERED: '1', YURLAPS_ELECTRON: '1' },
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
    });

    let buffer = '';
    let settled = false;
    const timer = setTimeout(() => {
      if (!settled) {
        settled = true;
        reject(new Error('Mesin YurLaps tidak merespons dalam 30 detik.'));
      }
    }, 30000);

    engine.stdout.setEncoding('utf8');
    engine.stdout.on('data', (chunk) => {
      buffer += chunk;
      let nl;
      while ((nl = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, nl).trim();
        buffer = buffer.slice(nl + 1);
        if (!line) continue;
        if (IS_DEV) console.log('[mesin]', line);
        if (line.startsWith('{')) {
          try {
            const msg = JSON.parse(line);
            if (msg.event === 'ready' && !settled) {
              settled = true;
              clearTimeout(timer);
              engineUrl = `http://${msg.host}:${msg.port}/`;
              resolve(engineUrl);
            }
          } catch {
            /* bukan JSON: log biasa, abaikan */
          }
        }
      }
    });

    engine.stderr.setEncoding('utf8');
    engine.stderr.on('data', (d) => {
      if (IS_DEV) process.stderr.write(`[mesin:err] ${d}`);
    });

    engine.on('error', (err) => {
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        reject(err);
      }
    });

    engine.on('exit', (code) => {
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        reject(new Error(`Mesin YurLaps berhenti (kode ${code}).`));
      }
      // Mesin mati mendadak -> tutup jendela supaya tidak menampilkan UI beku
      if (win && !win.isDestroyed() && !app.isQuiting) {
        dialog.showErrorBox(
          'YurLaps berhenti',
          `Mesin timing berhenti (kode ${code}). Aplikasi akan ditutup.\n` +
            'Jika ini berulang, jalankan YurLaps.exe dan kirimkan pesan galat yang muncul.'
        );
        app.isQuiting = true;
        app.quit();
      }
    });
  });
}

// ---------------------------------------------------------------------------
// 3. Jendela
// ---------------------------------------------------------------------------
function createWindow() {
  win = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1024,
    minHeight: 680,
    show: false,
    backgroundColor: '#f7f9fc',
    title: 'YurLaps',
    autoHideMenuBar: true,
    icon: path.join(ROOT, 'assets', 'yurlaps.ico'),
    webPreferences: {
      preload: path.join(__dirname, '..', 'preload', 'index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  win.setMenuBarVisibility(false);
  win.loadURL(engineUrl);

  win.once('ready-to-show', () => win.show());

  const pushFullscreen = () => {
    if (win && !win.isDestroyed()) {
      win.webContents.send('yurlaps:fullscreen', win.isFullScreen());
    }
  };
  win.on('enter-full-screen', pushFullscreen);
  win.on('leave-full-screen', pushFullscreen);

  // Tautan luar dibuka di browser, bukan di dalam jendela aplikasi
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  // F5/Ctrl+R sengaja dinonaktifkan: tidak ada "refresh" di aplikasi timing
  win.webContents.on('before-input-event', (event, input) => {
    const k = (input.key || '').toLowerCase();
    if (k === 'f5' || ((input.control || input.meta) && k === 'r')) {
      event.preventDefault();
    }
  });

  win.on('closed', () => {
    win = null;
  });
}

// ---------------------------------------------------------------------------
// 4. Siklus hidup
// ---------------------------------------------------------------------------
app.isQuiting = false;

// --- IPC dari preload ------------------------------------------------------
ipcMain.on('yurlaps:toggle-fullscreen', () => {
  if (!win) return;
  win.setFullScreen(!win.isFullScreen());
});
ipcMain.handle('yurlaps:is-fullscreen', () => (win ? win.isFullScreen() : false));

// Satu instance saja: dua instance akan berebut port & data
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (win) {
      if (win.isMinimized()) win.restore();
      win.focus();
    }
  });

  app.whenReady().then(async () => {
    Menu.setApplicationMenu(null);
    try {
      await startEngine();
      createWindow();
      const { autoUpdater } = require('electron-updater');
      const { configureUpdates } = require('./updates');
      const backend = async action => {
        const response = await fetch(engineUrl + 'api/update/' + action, { method: 'POST',
          headers: { 'Content-Type': 'application/json' }, body: '{}' });
        const result = await response.json();
        if (!response.ok || !result.ok) throw new Error(result.error || 'Pembaruan belum dapat dipasang');
      };
      configureUpdates({ app, ipcMain, autoUpdater, getWindow: () => win,
        prepare: () => backend('prepare'), cancel: () => backend('cancel'),
        stopEngine: () => new Promise((resolve, reject) => {
          const child = engine;
          if (!child || child.exitCode !== null) return reject(new Error('Mesin timing tidak aktif'));
          app.isQuiting = true;
          const timer = setTimeout(() => {
            child.removeListener('exit', done);
            app.isQuiting = false;
            reject(new Error('Mesin belum berhenti. Tunggu penyimpanan selesai sebelum mencoba lagi.'));
          }, 20000);
          function done() { clearTimeout(timer); engine = null; resolve(); }
          child.once('exit', done);
          child.stdin.end();
        })
      });
    } catch (err) {
      dialog.showErrorBox('YurLaps gagal dijalankan', String(err && err.message ? err.message : err));
      app.isQuiting = true;
      app.quit();
    }
  });
}

function stopEngine() {
  if (!engine) return;
  try {
    engine.stdin.end();          // mesin berhenti saat stdin ditutup
  } catch { /* sudah mati */ }
  const child = engine;
  engine = null;
  setTimeout(() => {
    try {
      if (!child.killed) child.kill();
    } catch { /* sudah mati */ }
  }, 3000);
}

app.on('before-quit', () => {
  app.isQuiting = true;
  stopEngine();
});

app.on('window-all-closed', () => {
  app.isQuiting = true;
  stopEngine();
  app.quit();
});

process.on('exit', stopEngine);
