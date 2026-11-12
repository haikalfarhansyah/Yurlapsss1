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
/** @type {BrowserWindow | null} */
let splash = null;
let engineUrl = '';
let splashShownAt = 0;
let mainRevealed = false;
let revealTimer = null;

// Splash minimal tampil beberapa saat supaya tidak berkedip saat mesin cepat.
const SPLASH_MIN_MS = 600;
// Kalau halaman tidak pernah memberi tahu "siap", jendela utama tetap dibuka.
const REVEAL_FALLBACK_MS = 10000;

// ---------------------------------------------------------------------------
// 0. Jendela splash (logo + status) selama mesin Python menyiapkan diri
// ---------------------------------------------------------------------------
function createSplash() {
  splash = new BrowserWindow({
    width: 460,
    height: 320,
    frame: false,
    resizable: false,
    movable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    show: false,
    backgroundColor: '#0b6685',
    title: 'YurLaps',
    icon: path.join(ROOT, 'assets', 'yurlaps.ico'),
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  splash.setMenuBarVisibility(false);
  splash.loadFile(path.join(__dirname, '..', 'splash.html'));
  splash.once('ready-to-show', () => {
    if (splash && !splash.isDestroyed()) splash.show();
  });
  // Tombol "Tutup" pada tampilan error: tutup jendela ini lalu keluar.
  splash.on('closed', () => {
    splash = null;
    if (!mainRevealed) {
      app.isQuiting = true;
      app.quit();
    }
  });
  splashShownAt = Date.now();
}

function splashStatus(message) {
  if (!splash || splash.isDestroyed()) return;
  splash.webContents.executeJavaScript(
    `window.setSplashStatus && window.setSplashStatus(${JSON.stringify(String(message || ''))})`)
    .catch(() => {});
}

function splashError(message) {
  if (!splash || splash.isDestroyed()) return;
  splash.webContents.executeJavaScript(
    `window.setSplashError && window.setSplashError(${JSON.stringify(String(message || ''))})`)
    .catch(() => {});
}

function closeSplash() {
  if (!splash) return;
  const target = splash;
  splash = null;
  try { target.destroy(); } catch { /* sudah tertutup */ }
}

/** Tampilkan jendela utama lalu tutup splash (dengan durasi minimum). */
function revealMainWindow() {
  if (mainRevealed) return;
  mainRevealed = true;
  if (revealTimer) { clearTimeout(revealTimer); revealTimer = null; }
  // Splash tidak lagi mengambang di atas jendela utama.
  if (splash && !splash.isDestroyed()) splash.setAlwaysOnTop(false);
  if (win && !win.isDestroyed()) win.show();
  const wait = Math.max(0, SPLASH_MIN_MS - (Date.now() - splashShownAt));
  setTimeout(closeSplash, wait);
}

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
            if (msg.event === 'progress') {
              // Status proses dari mesin Python ditampilkan di splash.
              splashStatus(msg.message);
            }
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
      autoplayPolicy: 'no-user-gesture-required',
    },
  });

  win.setMenuBarVisibility(false);
  win.loadURL(engineUrl);

  // Jendela utama tetap tersembunyi sampai halaman memberi tahu data sudah
  // siap; kalau sinyal tidak datang, buka juga setelah batas waktu.
  win.once('ready-to-show', () => {
    if (!revealTimer) revealTimer = setTimeout(revealMainWindow, REVEAL_FALLBACK_MS);
  });

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
ipcMain.handle('yurlaps:open-external', (_event, url) => {
  if (!/^https?:\/\//i.test(url)) throw new Error('URL login tidak valid');
  return shell.openExternal(url);
});
// Halaman memberitahu data sudah siap -> tampilkan jendela utama, tutup splash.
ipcMain.on('yurlaps:app-ready', () => revealMainWindow());

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
    createSplash();
    splashStatus('Menyiapkan YurLaps…');
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
      const message = String(err && err.message ? err.message : err);
      splashError(message);
      // Beri waktu pengguna membaca pesan di splash sebelum jendela ditutup.
      setTimeout(() => {
        app.isQuiting = true;
        app.quit();
      }, 12000);
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
  closeSplash();
  stopEngine();
});

app.on('window-all-closed', () => {
  app.isQuiting = true;
  closeSplash();
  stopEngine();
  app.quit();
});

process.on('exit', stopEngine);
