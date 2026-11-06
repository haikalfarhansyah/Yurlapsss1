'use strict';
/**
 * Uji shell Electron tanpa layar (headless).
 *
 * Yang diperiksa — semuanya hal yang tidak bisa dijamin oleh uji Python:
 *   1. mesin Python benar-benar dijalankan dan mengirim {"event":"ready"}
 *   2. jendela memuat URL mesin dan selesai memuat (did-finish-load)
 *   3. UI yang tampil adalah UI YurLaps (menu Quick Race/History/Member/Track/Decoder ada)
 *   4. mesin dimatikan rapi saat aplikasi ditutup (tidak ada proses yatim)
 *   5. port hanya 127.0.0.1 (tidak terbuka ke jaringan)
 *
 * Jalankan:  npx electron test/smoke.js   (di folder desktop/)
 */
const { app, BrowserWindow } = require('electron');
const { spawn } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');

const REPO = path.join(__dirname, '..', '..');
const results = [];

function check(name, ok, detail = '') {
  results.push({ name, ok, detail });
  console.log(`${ok ? 'LULUS' : 'GAGAL'}  ${name}${detail ? ' — ' + detail : ''}`);
}

function waitReady(child) {
  return new Promise((resolve, reject) => {
    let buf = '';
    const timer = setTimeout(() => reject(new Error('tidak ada ready dalam 40 s')), 40000);
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', (d) => {
      buf += d;
      const nl = buf.indexOf('\n');
      if (nl < 0) return;
      const line = buf.slice(0, nl).trim();
      try {
        const msg = JSON.parse(line);
        if (msg.event === 'ready') {
          clearTimeout(timer);
          resolve(msg);
        }
      } catch {
        /* log biasa */
      }
    });
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', (d) => process.stderr.write(`[mesin:err] ${d}`));
    child.on('exit', (c) => {
      clearTimeout(timer);
      reject(new Error(`mesin keluar (kode ${c})`));
    });
  });
}

app.whenReady().then(async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'yurlaps-el-'));
  const py = process.env.YURLAPS_PYTHON || 'python3';
  const engine = process.env.YURLAPS_ENGINE;
  const child = spawn(engine || py, engine ? ['--electron'] : ['-m', 'app', '--electron'], {
    cwd: REPO,
    env: { ...process.env, YURLAPS_DATA: dataDir, PYTHONUNBUFFERED: '1' },
    stdio: ['pipe', 'pipe', 'pipe'],
  });

  let msg;
  try {
    msg = await waitReady(child);
    check('mesin mengirim ready', true, `${msg.host}:${msg.port}`);
  } catch (err) {
    check('mesin mengirim ready', false, String(err.message));
    app.exit(1);
    return;
  }

  check('server hanya di 127.0.0.1', msg.host === '127.0.0.1', msg.host);
  check('port dipilih otomatis (bukan 8080)', Number(msg.port) !== 8080, String(msg.port));

  const win = new BrowserWindow({
    show: false,
    width: 1400,
    height: 900,
    webPreferences: {
      preload: path.join(__dirname, '..', 'src', 'preload', 'index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  const url = `http://${msg.host}:${msg.port}/`;

  const loaded = new Promise((resolve) => {
    win.webContents.once('did-finish-load', () => resolve(true));
    win.webContents.once('did-fail-load', (_e, code, desc) => resolve(`gagal ${code} ${desc}`));
    setTimeout(() => resolve('timeout'), 30000);
  });
  win.loadURL(url);

  const state = await loaded;
  check('jendela memuat UI', state === true, String(state));

  let info = {};
  try {
    info = await win.webContents.executeJavaScript(`(() => ({
      title: document.title,
      quickrace: !!document.querySelector('[data-view="quickrace"]'),
      history: !!document.querySelector('[data-view="history"]'),
      member: !!document.querySelector('[data-view="member"]'),
      track: !!document.querySelector('[data-view="track"]'),
      decoder: !!document.querySelector('[data-view="decoder"]'),
      shell: !!(window.yurlapsShell && window.yurlapsShell.isAppWindow),
      bodyText: (document.body.innerText || '').slice(0, 120).replace(/\\s+/g, ' '),
    }))()`);
  } catch (err) {
    check('membaca isi UI', false, String(err.message));
  }

  check('judul halaman YurLaps', /YurLaps/i.test(info.title || ''), info.title);
  check('menu Quick Race ada', !!info.quickrace);
  check('menu History ada', !!info.history);
  check('menu Member ada', !!info.member);
  check('menu Track ada', !!info.track);
  check('menu Decoder ada', !!info.decoder);
  check('preload aktif (mode aplikasi)', !!info.shell);
  check('UI ada isinya', (info.bodyText || '').length > 10, info.bodyText);

  // ---- tutup: mesin harus ikut berhenti ----
  let exited = false;
  child.on('exit', () => { exited = true; });
  child.stdin.end();
  await new Promise((r) => setTimeout(r, 3000));
  check('mesin berhenti saat jendela ditutup', exited);
  try { if (!exited) child.kill(); } catch { /* sudah mati */ }

  const gagal = results.filter((r) => !r.ok);
  console.log('');
  console.log(gagal.length === 0
    ? 'SEMUA UJI JENDELA ELECTRON LULUS [OK]'
    : `ADA ${gagal.length} UJI GAGAL`);
  app.exit(gagal.length === 0 ? 0 : 1);
});

// Jangan biarkan jendela yang terlanjur terbuka menggantung proses
app.on('window-all-closed', () => {});
