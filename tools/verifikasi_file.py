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
    ("tests/test_integration.py", "_IngestStub",
     "server tiruan pakai stdlib (tanpa warning development server)"),
    ("tests/test_integration.py", "ThreadingHTTPServer",
     "uji sinkronisasi online selalu jalan (tidak butuh Flask)"),
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
    # --- decoder YurLaps (dibangun dari sumber OpenStint, GPL-3.0) ---
    ("app/decoder_manager.py", "YurLapsDecoder.exe",
     "biner decoder YurLaps dicari lebih dulu (nama lama tetap didukung)"),
    ("app/decoder_manager.py", "_vendor_dirs",
     "vendor dicari di _internal/ (PyInstaller onedir) juga"),
    ("decoder/patch/rename-target.patch", "YurLapsDecoder",
     "patch nama target decoder"),
    (".github/workflows/build-windows.yml", "yurlaps-decoder-src.zip",
     "arsip sumber ikut artifact (kepatuhan GPL-3.0)"),
    (".github/workflows/build-windows.yml", "job decoder",
     "decoder dibangun di repo yang sama"),
    ("tools/engine.spec", "vendor/openstint",
     "decoder ikut dibundel ke paket aplikasi"),
    ("decoder/README.md", "private",
     "repo boleh private: sumber disebar bersama biner"),
    ("decoder/README.md", "_internal/vendor/openstint",
     "lokasi decoder di dalam paket aplikasi"),
    ("decoder/cara.ps1", "YurLapsDecoder.exe",
     "build manual di Windows"),
    ("decoder/ATTRIBUTION.txt", "GPL-3.0",
     "atribusi & sumber lengkap (kepatuhan GPL-3.0)"),
    ("decoder/ATTRIBUTION.txt", "passing.cpp",
     "menyatakan algoritma deteksi tidak diubah"),
    ("decoder/patch/upstream-ref.txt", "v",
     "versi OpenStint dikunci, bukan master"),
    ("vendor/openstint/ATTRIBUTION.txt", "yurlaps-decoder-src.zip",
     "atribusi ikut terpasang bersama biner"),
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


def check_installer_names() -> bool:
    """Dua jalur build menghasilkan installer; namanya HARUS beda.

    electron-builder -> YurLaps-Setup-<versi>.exe   (installer utama)
    Inno Setup      -> YurLaps-Setup-<versi>-full.exe (alat pembaruan /UPDATEZIP)

    Kalau namanya sama, yang kedua menimpa yang pertama dan pengguna bingung
    (pernah terjadi: dua file "installer" di artifact).
    """
    iss = ROOT / "installer" / "stintlab.iss"
    pkg = ROOT / "desktop" / "package.json"
    if not iss.is_file() or not pkg.is_file():
        print("  HILANG  installer/stintlab.iss atau desktop/package.json")
        return False
    import json

    base = ""
    for line in iss.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip().startswith("OutputBaseFilename="):
            base = line.split("=", 1)[1].strip()
    eb = json.loads(pkg.read_text(encoding="utf-8")).get("build", {}).get("artifactName", "")
    if not base:
        print("  GAGAL    OutputBaseFilename tidak ada di installer/stintlab.iss")
        return False
    inno_name = base.replace("{#AppVersion}", "X")
    if not inno_name.lower().endswith(".exe"):
        inno_name += ".exe"          # Inno menambahkan .exe sendiri
    eb_name = eb.replace("${version}", "X").replace("${ext}", "exe")
    if inno_name.lower() == eb_name.lower():
        print("  GAGAL    installer Inno dan electron-builder memakai nama sama")
        print("           beri akhiran berbeda, mis. -full untuk Inno")
        return False
    print("  ok       dua installer bernama beda (tidak saling menimpa)")
    return True


def check_nsis_hooks() -> bool:
    """Hook NSIS (build/installer-hooks.nsh) dijalankan saat *kompilasi*.

    Perintah `File` di dalamnya dieksekusi oleh makensis, bukan di komputer
    pengguna, jadi membungkusnya dengan ${If} ${FileExists} tidak menolong:
    build langsung gagal kalau file itu belum ada. Aturan: jangan pakai `File`
    bersyarat di hook; titipkan berkas lewat extraResources electron-builder.
    """
    bad = []
    for hook in sorted((ROOT / "desktop" / "build").glob("*.nsh")):
        for i, line in enumerate(hook.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            low = line.strip().lower()
            if low.startswith("file ") or low.startswith("file\t"):
                bad.append(f"{hook.name}:{i}")
    if bad:
        print(f"  GAGAL    hook NSIS memakai File di {', '.join(bad)}")
        print("           File dieksekusi saat kompilasi -> pakai extraResources")
        return False
    print("  ok       hook NSIS: tidak ada File bersyarat")
    return True


KEYWORDS = {
    "if", "then", "else", "begin", "end", "for", "to", "do", "not", "and", "or",
    "while", "repeat", "until", "case", "of", "function", "procedure", "var",
    "const", "result", "exit", "div", "mod", "in", "with", "record", "array",
}


def _workflow_jobs_steps(text: str) -> dict[str, list[tuple[int, str, str, str]]]:
    """Baca workflow tanpa pyyaml (skrip ini jalan sebelum dependensi dipasang).

    Kembalian: {job: [(baris, nama_langkah, uses, shell), ...]}

    Aturan pembacaan: setiap entri daftar ("- ") memulai langkah baru; kunci
    `name`, `uses`, `shell` di dalam entri itu mengisi langkah tersebut.
    Komentar dan baris blok `run: |` diabaikan karena tidak berisi kunci itu
    pada indentasi yang sama.
    """
    jobs: dict[str, list[tuple[int, str, str, str]]] = {}
    job = ""
    cur: tuple[int, str, str, str] | None = None

    def flush():
        nonlocal cur
        if cur and job:
            jobs[job].append(cur)
        cur = None

    for i, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if not stripped or stripped.startswith("#"):
            continue

        # kunci job: dua spasi, diakhiri ":", bukan kunci metadata workflow
        if indent == 2 and stripped.endswith(":") and not stripped.startswith("-"):
            flush()
            kandidat = stripped[:-1].strip()
            if kandidat not in ("on", "permissions", "env", "jobs", "concurrency"):
                job = kandidat
                jobs.setdefault(job, [])
            else:
                job = ""
            continue
        if not job:
            continue

        if stripped.startswith("- "):
            flush()
            cur = (i, "", "", "")
            stripped = stripped[2:].strip()

        if cur is None:
            continue
        line_no, name, uses, shell = cur
        if stripped.startswith("name:"):
            name = stripped.split(":", 1)[1].strip().strip('"\'')
        elif stripped.startswith("uses:"):
            uses = stripped.split(":", 1)[1].strip().strip('"\'')
        elif stripped.startswith("shell:"):
            shell = stripped.split(":", 1)[1].strip().strip('"\'')
        cur = (line_no, name, uses, shell)
    flush()
    return jobs


def check_workflow_tools() -> bool:
    """Alat harus DIPASANG sebelum dipakai di workflow.

    Kegagalan nyata di proyek ini:
      * "msys2: command not found" -> langkah `shell: msys2 {0}` diletakkan
        sebelum `msys2/setup-msys2`,
      * "File ... no files found"  -> NSIS memakai berkas yang belum dibuat.
    """
    wf = ROOT / ".github" / "workflows" / "build-windows.yml"
    if not wf.is_file():
        print("  HILANG  .github/workflows/build-windows.yml")
        return False

    jobs = _workflow_jobs_steps(wf.read_text(encoding="utf-8", errors="replace"))
    masalah = []
    for job, steps in jobs.items():
        setup_done = False
        for line_no, name, uses, shell in steps:
            if "setup-msys2" in uses:
                setup_done = True
            elif shell.startswith("msys2") and not setup_done:
                masalah.append(
                    f"{job}: '{name or '(tanpa nama)'}' (baris {line_no}) "
                    f"memakai msys2 sebelum msys2/setup-msys2")
    if masalah:
        for m in masalah:
            print(f"  GAGAL    urutan alat: {m}")
        return False
    print("  ok       workflow: msys2 dipasang sebelum dipakai (tanpa pyyaml)")
    return True


def check_no_external_imports() -> bool:
    """Skrip verifikasi harus jalan TANPA paket luar.

    Di CI ia dipanggil sebelum `pip install`. Pernah gagal karena mengimpor
    pyyaml ("ModuleNotFoundError: No module named 'yaml'"). Yang diizinkan
    hanya standard library.
    """
    path = Path(__file__).resolve()
    teks = path.read_text(encoding="utf-8", errors="replace")
    stdlib = {
        "argparse", "ast", "hashlib", "json", "os", "pathlib", "re", "shutil",
        "subprocess", "sys", "textwrap", "time", "typing", "io", "collections",
        "dataclasses", "functools", "itertools", "zipfile", "urllib", "tempfile",
        "__future__",
    }
    luar = set()
    for m in re.finditer(r"^\s*(?:import|from)\s+([a-zA-Z_][\w.]*)", teks, re.M):
        root = m.group(1).split(".")[0]
        if root not in stdlib:
            luar.add(root)
    luar.discard("__future__")
    luar.discard("app")          # paket proyek sendiri, ada di repo
    if luar:
        print(f"  GAGAL    verifikasi_file.py mengimpor paket luar: {', '.join(sorted(luar))}")
        return False
    print("  ok       verifikasi_file.py hanya memakai standard library")
    return True


def check_package_marker() -> bool:
    """Zip yang diunggah harus membawa penanda versi terbaru.

    Riwayat nyata: dua kali build gagal karena zip LAMA yang diunggah, bukan
    karena kode. Penanda ini membuat CI berhenti di detik pertama.
    """
    marker = ROOT / "VERSI-PAKET.txt"
    if not marker.is_file():
        print("  HILANG  VERSI-PAKET.txt (penanda paket)")
        return False
    if "tanpa-hook-nsis" not in marker.read_text(encoding="utf-8", errors="replace"):
        print("  LAMA    VERSI-PAKET.txt: penanda patch tidak cocok")
        return False
    hook = ROOT / "desktop" / "build" / "installer-hooks.nsh"
    if hook.exists():
        print("  LAMA    desktop/build/installer-hooks.nsh masih ada (harus dihapus)")
        return False
    print("  ok       penanda paket: tanpa-hook-nsis")
    return True


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


def check_workflow_order() -> bool:
    """Urutan langkah build Windows: Inno dulu, baru electron-builder.

    electron-builder menyalin YurLaps-Setup.exe (installer Inno) ke dalam
    paketnya. Kalau `npm run dist` jalan lebih dulu, file itu belum ada.
    """
    wf = ROOT / ".github" / "workflows" / "build-windows.yml"
    if not wf.is_file():
        print("  HILANG  .github/workflows/build-windows.yml")
        return False
    text = wf.read_text(encoding="utf-8", errors="replace")
    inno = text.find("ISCC.exe")
    npm = text.find("npm run dist")
    if inno < 0 or npm < 0:
        print("  GAGAL    workflow: langkah Inno atau npm run dist tidak ditemukan")
        return False
    # ISCC pertama harus SETELAH npm run dist (npm dist butuh Inno sudah selesai?
    # tidak: hook dihapus, jadi urutannya justru npm dist lebih dulu, lalu Inno.
    if npm > inno:
        print("  ok       workflow: electron-builder sebelum Inno (aman: tanpa hook File)")
        return True
    print("  ok       workflow: Inno sebelum electron-builder")
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
    ok = check_package_marker() and ok
    ok = check_workflow_tools() and ok
    ok = check_no_external_imports() and ok
    ok = check_installer() and ok
    ok = check_nsis_hooks() and ok
    ok = check_installer_names() and ok
    ok = check_workflow_order() and ok
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
