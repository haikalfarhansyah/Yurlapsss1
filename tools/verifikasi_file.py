"""Cek cepat: apakah file di repo sudah versi terbaru?

Dijalankan di CI sebelum tes. Kalau ada file lama yang tertinggal (mis. karena
penyalinan gagal / nama file berubah), skrip ini berhenti dengan daftar yang
jelas — bukan error membingungkan jauh di dalam tes.

Jalankan manual:  python tools/verifikasi_file.py
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (file, penanda yang HARUS ada, keterangan)
CHECKS = [
    ("installer/stintlab.iss", "UseWdiSimple",
     "penjaga entri driver (fix error 'line 47 file not found')"),
    ("installer/stintlab.iss", "#if HasIcon",
     "ikon opsional (tidak wajib ada)"),
    ("tests/test_smoke.py", "detail = (f\"counter=",
     "diagnosis ZMQ saat gagal (counter/passes/online)"),
    ("tests/test_smoke.py", "deadline = time.time() +",
     "tes ZMQ tahan-waktu (tidak flaky)"),
    ("app/decoder_manager.py", "def command_for",
     "menjalankan decoder berbentuk skrip di Windows"),
    ("tools/buat_update_manifest.py", "def find_asset",
     "pencarian zip portable di dist/ dan root"),
    ("tools/buat_update_manifest.py", 'stdout.reconfigure(errors="replace")',
     "aman di konsol Windows cp1252"),
    ("stintlab.spec", '"webview", "clr", "pythonnet"',
     "pywebview/pythonnet dikecualikan dari bundel (anti-crash .exe)"),
    ("app/desktop.py", "def launch_app_window",
     "jendela aplikasi via Edge/Chrome --app"),
    ("app/config.py", "def effective_decoder_args",
     "gain & opsi radio disusun dari setelan (UI Decoder)"),
    ("app/decoder_manager.py", "def apply_gain",
     "ubah gain -> simpan -> restart openstint.exe"),
    ("app/engine.py", "def log_pass",
     "tabel lintasan mentah (P): id, rssi, mer, hit, durasi, timecode"),
    ("app/engine.py", "def on_timesync", "pesan T time-sync OpenStint"),
    ("app/webapp.py", '"/api/decoder/gain"', "endpoint ubah gain + restart decoder"),
    ("app/webapp.py", '"/api/decoder/stats"', "endpoint diagnostik decoder"),
    ("app/static/index.html", "function renderDecoder",
     "UI halaman Decoder (gain, noise, tabel lintasan)"),
    ("app/static/index.html", 'data-view="practice"', "menu Practice"),
    ("app/static/index.html", 'data-view="decoder"', "menu Decoder"),
    ("app/main.py", "_safe_console",
     "anti-crash karakter khusus di konsol Windows"),
    ("app/static/favicon.ico", None,
     "ikon aplikasi & installer (binary)"),
    ("assets/yurlaps.ico", None, "ikon YurLaps untuk .exe & installer (binary)"),
    ("app/static/assets/yurlaps-logo.png", None, "logo YurLaps (sidebar & ikon)"),
    ("app/config.py", 'APP_NAME = "YurLaps"', "nama aplikasi YurLaps"),
    ("app/config.py", "def migrate_legacy_data", "data lama (0.2.x) dimigrasikan"),
    ("app/native.py", "class YurLapsWindow", "UI native Tkinter (tanpa browser)"),
    ("app/native.py", "start_native", "mesin jalan tanpa server web"),
    ("app/main.py", "Sinkron ke website: mati (lokal saja)",
     "tanpa internet kalau sinkron web tidak diminta"),
    ("app/main.py", 'p.add_argument("--web"', "mode web opsional (--web) untuk HP/TV"),
    ("run_stintlab.py", '"--native"', ".exe default membuka UI native"),
    ("stintlab.spec", '"app.native"', "modul native ikut dibundel"),
    ("app/static/index.html", "const tsRaw = (ms) =>",
     "log Decoder: timestamp mentah ms"),
    ("app/static/index.html", 'data-view="driver"', "menu Driver"),
    ("app/static/index.html", "/static/assets/yurlaps-logo.png", "logo YurLaps di UI"),
    ("app/webapp.py", '/api/driver/delete', "endpoint hapus pemetaan driver"),
    ("app/webapp.py", '/api/session"', "tukar token jadi cookie sesi"),
    ("app/engine.py", "def lap_delta_ms", "lap time dari timestamp decoder (ms)"),
    ("app/decoder_manager.py", "_waiting_for_dongle",
     "auto-connect: menunggu YurLaps Decoder tanpa menunggu 30 s"),
    ("app/updater.py", "def install_command",
     "perbarui lewat installer (pintasan & uninstall tetap sinkron)"),
    ("installer/stintlab.iss", "/UPDATEZIP=", "installer mendukung mode pembaruan"),
    (".github/workflows/build-windows.yml", "actions/checkout@v7",
     "action Node 24 (menghilangkan warning)"),
    ("stintlab.spec", 'excludes=["PIL"', "tkinter tidak dikecualikan (UI native)"),
]

STALE = [
    ("tools/buat_update_manifest.py", "→",
     "karakter panah -> crash cp1252 (harus dihapus)"),
    ("tests/test_smoke.py", 'rx_counts["P"] >= 2',
     "syarat lama >= 2 (bikin gagal kalau hanya 1 pesan masuk)"),
    ("installer/stintlab.iss", 'Name: "id"; MessagesFile',
     "dua bahasa dengan file pesan sama (ditolak Inno 6.7)"),
]


def installer_no_dist_check(text: str) -> bool:
    """[Code] tidak boleh memeriksa folder 'dist' (bug installer 0.2.7).

    Setup.exe yang sudah didistribusikan ada di folder unduhan pengguna, jadi
    pemeriksaan path build relatif terhadap folder setup akan selalu gagal
    dan menghalangi pemasangan.
    """
    code = text.split("[Code]", 1)[-1]
    for line in code.splitlines():
        clean = line.strip()
        if clean.startswith("//"):
            continue
        if "{src}" in clean and "dist" in clean.lower():
            return False
        if "InitializeSetup" in clean and "dist" in clean.lower():
            return False
    return True


def icon_guarded(text: str) -> bool:
    """SetupIconFile hanya aman kalau didahului '#if HasIcon'."""
    for m in re.finditer(r"^SetupIconFile=", text, re.MULTILINE):
        before = text[:m.start()]
        if "#if HasIcon" not in before.splitlines()[-3:][0] and \
           "#if HasIcon" not in "\n".join(before.splitlines()[-3:]):
            return False
    return True


# Modul yang wajib ada. Kalau salah satu hilang, aplikasi tidak bisa jalan
# (mis. karena folder app/ tertimpa paket pembaruan yang tidak lengkap).
REQUIRED = [
    "app/__init__.py", "app/__main__.py", "app/config.py", "app/decoder.py",
    "app/decoder_manager.py", "app/desktop.py", "app/devices.py", "app/engine.py",
    "app/main.py", "app/net.py", "app/online.py", "app/storage.py",
    "app/updater.py", "app/webapp.py",
    "app/static/index.html",
    "app/static/assets/yurlaps-logo.png", "app/static/assets/yurlaps.ico",
    "app/static/assets/favicon.png",
    "assets/yurlaps.ico",
    "run_stintlab.py", "stintlab.spec", "requirements.txt",
    "run_stintlab_win8.py", "stintlab-win8.spec",
    "jalankan-win8.bat",
    "tests/test_smoke.py", "tests/test_integration.py", "tests/test_manager.py",
]


def check_required() -> bool:
    missing = [r for r in REQUIRED if not (ROOT / r).is_file()]
    if not missing:
        print(f"  ok      {len(REQUIRED)} file wajib lengkap")
        return True
    print("  HILANG  file wajib berikut tidak ada di repo:")
    for m in missing:
        print(f"            - {m}")
    print("          -> paket pembaruan sepertinya menimpa folder app/ secara")
    print("             tidak lengkap. Salin ULANG seluruh folder app/ (bukan")
    print("             hanya beberapa file), lalu push lagi.")
    return False


def check_imports() -> bool:
    """Setiap 'from .x import' / 'from app.x import' harus punya file-nya."""
    ok = True
    for f in sorted((ROOT / "app").glob("*.py")):
        text = f.read_text(encoding="utf-8", errors="replace")
        for target in re.findall(r"from \.([a-z_]+) import", text):
            if not (ROOT / "app" / f"{target}.py").is_file():
                print(f"  PATAH   {f.name}: 'from .{target} import ...' "
                      f"tetapi app/{target}.py tidak ada")
                ok = False
        for target in re.findall(r"from app\.([a-z_]+) import", text):
            if not (ROOT / "app" / f"{target}.py").is_file():
                print(f"  PATAH   {f.name}: 'from app.{target} import ...' "
                      f"tetapi app/{target}.py tidak ada")
                ok = False
    if ok:
        print("  ok      semua impor internal utuh")
    return ok


def fingerprint(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    except Exception:
        return "(tidak ada)"


def main() -> int:
    print("Verifikasi versi & kelengkapan file\n" + "-" * 56)
    ok = check_required()
    ok = check_imports() and ok
    for rel, marker, desc in CHECKS:
        p = ROOT / rel
        if not p.is_file():
            print(f"  HILANG  {rel:38s} {desc}")
            ok = False
            continue
        if marker is None:
            print(f"  ok      {rel:38s} {desc} [{fingerprint(p)}]")
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if marker in text:
            print(f"  ok      {rel:38s} {desc} [{fingerprint(p)}]")
        else:
            print(f"  LAMA    {rel:38s} {desc}")
            ok = False

    iss = ROOT / "installer/stintlab.iss"
    if iss.is_file() and not installer_no_dist_check(
            iss.read_text(encoding="utf-8", errors="replace")):
        print(f"  BUG     {'installer/stintlab.iss':38s} [Code] memeriksa folder dist "
              f"(installer akan gagal di komputer pengguna)")
        ok = False
    if iss.is_file() and not icon_guarded(iss.read_text(encoding="utf-8", errors="replace")):
        print(f"  LAMA    {'installer/stintlab.iss':38s} SetupIconFile tanpa penjaga #if")
        ok = False

    for rel, marker, desc in STALE:
        p = ROOT / rel
        if p.is_file() and marker in p.read_text(encoding="utf-8", errors="replace"):
            print(f"  LAMA    {rel:38s} {desc}")
            ok = False

    try:
        from app.config import APP_VERSION
        print(f"\n  Versi aplikasi di kode: {APP_VERSION}")
    except Exception:
        pass

    if not ok:
        print("\n" + "=" * 52)
        print("ADA FILE YANG HILANG / MASIH VERSI LAMA.")
        print("Perbaiki file yang ditandai HILANG / PATAH / LAMA di atas,")
        print("lalu commit & push lagi.")
        print("=" * 52)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
