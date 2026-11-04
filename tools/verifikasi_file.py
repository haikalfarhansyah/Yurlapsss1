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
    # --- jendela aplikasi Electron (jalur utama ala LapBeeps) ---
    ("desktop/src/main/index.js", "startEngine", "shell Electron menjalankan mesin Python"),
    ("desktop/src/main/index.js", "127.0.0.1", "server mesin hanya lokal (tanpa firewall)"),
    ("desktop/src/preload/index.js", "contextBridge", "preload aman (tanpa Node di UI)"),
    ("desktop/package.json", '"electron"', "jendela aplikasi Electron"),
    ("desktop/test/smoke.js", "SEMUA UJI JENDELA ELECTRON LULUS", "uji jendela tanpa layar"),
    ("tools/engine.spec", "app/static", "UI web ikut dibundel ke mesin"),
    ("tools/engine.spec", "tkinter", "mesin tanpa Tk (jendela oleh Electron)"),
    ("app/webapp.py", "_MEIPASS", "UI tetap ketemu saat dibundel"),
    ("app/main.py", "--electron", "mesin dijalankan shell Electron"),
    ("app/static/index.html", "Menyambungkan", "tidak macet di 'Tidak bisa memuat data'"),
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


def check_workflow_quotes() -> bool:
    """Blok `run: |` itu string literal YAML — `\\"` akan sampai apa adanya ke
    PowerShell dan memicu ParserError "Unexpected token". Ini pernah kejadian."""
    bad = []
    for wf in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        for i, line in enumerate(wf.read_text(encoding="utf-8").splitlines(), 1):
            if '\\"' in line:
                bad.append(f"{wf.name}:{i}")
    if bad:
        print(f"  GAGAL    escape kutip ganda di blok run: {', '.join(bad)}")
        print("           tulis kutip apa adanya; blok run: | tidak butuh escape")
        return False
    print("  ok       workflow: tidak ada escape kutip ganda di blok run")
    return True


KEYWORDS = {
    "if", "then", "else", "begin", "end", "for", "to", "do", "not", "and", "or",
    "while", "repeat", "until", "case", "of", "function", "procedure", "var",
    "const", "result", "exit", "div", "mod", "in", "with", "record", "array",
}


def check_installer() -> bool:
    """Installer dikompilasi ISCC di CI — tidak bisa diuji di sandbox Linux.
    Jadi periksa hal-hal yang dulu benar-benar membuat build merah:

    * istilah terlarang ("dongle", "RTL-SDR") yang tak boleh muncul di UI,
    * tanda kutip kosong `""` di dalam Parameters [Run] -> "Mismatched or
      misplaced quotes",
    * fungsi [Code] yang dipanggil sebelum dideklarasikan -> "Unknown
      identifier" (persis seperti UseZadig/UseZadigAndNotUpdate).
    """
    path = ROOT / "installer" / "stintlab.iss"
    if not path.is_file():
        print("  HILANG  installer/stintlab.iss")
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    problems = []

    low = text.lower()
    for word in ("dongle", "rtl-sdr"):
        if word in low:
            problems.append(f'kata terlarang "{word}" muncul di installer')

    for i, line in enumerate(text.splitlines(), 1):
        if line.strip().lower().startswith("parameters:") and '""' in line:
            problems.append(f"baris {i}: Parameters memuat kutip ganda bersarang")

    # Typo klasik: ewWaitUntilTerminate (kurang 'd') -> "Unknown identifier".
    if re.search(r"ewWaitUntilTerminate\b", text):
        problems.append("ewWaitUntilTerminate salah tulis (harus ewWaitUntilTerminated)")

    if "[Code]" in text:
        code = text.split("[Code]", 1)[1]
        # Komentar Pascal ({ ... }) berakhir pada "}" pertama, jadi kurung
        # kurawal di dalam komentar memotongnya -> syntax error. Di [Code]
        # kita hanya memakai komentar "//".
        for i, line in enumerate(code.splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith("{") and not stripped.startswith("{#"):
                problems.append(
                    f"[Code] baris {i}: komentar {{...}} dipakai; pakai // "
                    f"(kurung kurawal di dalam komentar memotong komentar)")
        defs: dict[str, int] = {}
        for i, line in enumerate(code.splitlines()):
            m = re.match(r"\s*(?:function|procedure)\s+(\w+)", line)
            if m:
                defs.setdefault(m.group(1).lower(), i)
        for i, line in enumerate(code.splitlines()):
            if re.match(r"\s*(?:function|procedure)\s+", line):
                continue
            for name in re.findall(r"(\w+)\s*\(", line):
                key = name.lower()
                if key in KEYWORDS or key not in defs:
                    continue
                if defs[key] > i:
                    problems.append(
                        f"[Code] '{name}' dipanggil sebelum dideklarasikan "
                        f"(pindahkan fungsinya ke atas)")
        if "param:UPDATEZIP" not in text:
            problems.append("[Code] /UPDATEZIP tidak dibaca lewat {param:UPDATEZIP}")

    if problems:
        for msg in problems:
            print(f"  GAGAL    installer: {msg}")
        return False
    print("  ok       installer: istilah, kutip [Run], urutan [Code] bersih")
    return True


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
    ok = check_workflow_quotes() and ok
    ok = check_installer() and ok
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
