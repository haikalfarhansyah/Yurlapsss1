"""Cek cepat: apakah file di repo sudah versi terbaru?

Dijalankan di CI sebelum tes. Kalau ada file lama yang tertinggal (mis. karena
penyalinan gagal / nama file berubah), skrip ini berhenti dengan daftar yang
jelas — bukan error membingungkan jauh di dalam tes.

Jalankan manual:  python tools/verifikasi_file.py
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (file, penanda yang HARUS ada, keterangan)
CHECKS = [
    ("app/static/quickrace.html", 'id="view-practice"', "halaman Practice produksi"),
    ("app/static/quickrace.html", 'id="view-sound"', "pengaturan Suara"),
    ("app/static/quickrace.html", 'id="view-track"', "pengaturan Track"),
    ("app/static/quickrace.js", "/api/cloud/password-login", "login API nyata"),
    ("app/static/sounds.js", "speechSynthesis", "TTS perangkat"),
    ("app/club.py", "BEGIN IMMEDIATE", "migrasi SQLite transaksional"),
    ("app/practice.py", "class PracticeRecorder", "recorder Practice"),
    ("app/race_config.py", "random_delay_max", "kontrak preset lengkap"),
    ("app/race_upload.py", "user_id", "identitas akun peserta race"),
    ("app/decoder_manager.py", "YurLapsDecoder.exe", "decoder bundel otomatis"),
    ("app/decoder_manager.py", "_vendor_dirs", "decoder di bundel PyInstaller"),
    ("app/webapp.py", "_MEIPASS", "UI dari bundel PyInstaller"),
    ("app/webapp.py", '"/api/races/delete"', "sinkron penghapusan race"),
    ("app/webapp.py", '"/api/event/schedule"', "API jadwal event"),
    ("app/schedule.py", "def compute_schedule", "hitung jam mulai jadwal"),
    ("app/schedule.py", "def build_groups", "pembagian grup jadwal"),
    ("app/schedule_report.py", "render_schedule_pdf", "PDF jadwal event"),
    ("app/static/event.js", "eventSchedule", "tab jadwal di halaman Event"),
    ("desktop/src/main/index.js", "startEngine", "shell Electron menjalankan mesin"),
    ("desktop/src/main/index.js", "createSplash", "splash layar saat aplikasi dibuka"),
    ("desktop/src/main/index.js", "yurlaps:app-ready", "splash ditutup setelah data siap"),
    ("desktop/src/splash.html", "setSplashStatus", "status proses di splash"),
    ("desktop/src/preload/index.js", "appReady", "jendela aplikasi melapor data siap"),
    ("app/main.py", '"event": "progress"', "status proses mesin ke shell"),
    ("app/updater.py", "MSG_NO_RELEASE", "pesan ramah saat belum ada rilis"),
    ("desktop/src/main/updates.js", "friendlyError", "error pembaruan ringkas untuk operator"),
    ("desktop/src/main/updates.js", "prepare", "pembaruan menunggu penyimpanan"),
    ("desktop/src/preload/index.js", "contextBridge", "preload terisolasi"),
    ("tools/engine.spec", "app/static", "UI dibundel bersama mesin"),
    ("tools/engine.spec", "vendor/openstint", "decoder ikut installer"),
    ("tools/engine.spec", "app.schedule", "mesin jadwal ikut build beku"),
    ("tools/engine.spec", "app.schedule_report", "PDF jadwal ikut build beku"),
    ("run_stintlab.py", '"--app-window"', "launcher menggunakan UI lengkap"),
    (".github/workflows/build-windows.yml", "yurlaps-decoder-src.zip", "sumber decoder GPL ikut artifact"),
    (".github/workflows/build-windows.yml", '"event": "progress"', "uji mesin baca semua baris status"),
    ("decoder/ATTRIBUTION.txt", "GPL-3.0", "atribusi decoder"),
    ("vendor/openstint/ATTRIBUTION.txt", "yurlaps-decoder-src.zip", "atribusi terpasang bersama decoder"),
    ("app/static/assets/yurlaps-logo.png", None, "logo aplikasi"),
    ("assets/yurlaps.ico", None, "ikon installer"),
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
    "app/static/quickrace.html", "app/static/quickrace.js", "app/static/sounds.js",
    "app/practice.py", "app/race.py", "app/race_results.py", "app/race_config.py",
    "app/schedule.py", "app/schedule_report.py",
    "app/cloud.py", "app/club.py", "app/member_sync.py", "app/race_upload.py",
    "docs/PRODUCTION-CHECKLIST.md",
    "app/static/assets/yurlaps-logo.png", "app/static/assets/yurlaps.ico",
    "app/static/assets/favicon.png",
    "assets/yurlaps.ico",
    "run_stintlab.py", "stintlab.spec", "requirements.txt",
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


def check_app_offline() -> bool:
    """Aplikasi Quick Race nyata (app/) harus tetap full offline & konsisten.

    Yang dijaga di sini adalah kesalahan yang sudah pernah benar-benar terjadi:
    - UI menyimpan data club di localStorage (harus SQLite lewat API),
    - state sesi disimpan di handler HTTP sehingga hilang tiap permintaan,
    - pesan tombol Unggah menjanjikan pengiriman padahal belum ada jaringan,
    - pencatatan race kehilangan timestamp decoder atau acuan GO.
    """
    import re as _re
    ok = True
    perlu = {
        "app/club.py": ["class ClubStore", "def simpan_race", "def races",
                        "BEGIN IMMEDIATE", "AS n_race"],
        "app/race.py": ["class RaceEngine", "def catat", "def klasemen",
                        "def simpan", "decoder_ts", "start_decoder_ts"],
        "app/static/quickrace.html": ['id="cdLayer"', 'id="modalSave"',
                                      'id="view-history"', 'id="modalPeserta"',
                                      'id="view-track"', "quickrace.js"],
        "app/static/quickrace.js": ["/api/race/start", "/api/race/go",
                                    "/api/race/finish", "/api/race/save",
                                    "/api/club/bootstrap", "RUTE_POST"],
        "app/webapp.py": ["/api/race/state", "/api/race/pass",
                          "/api/tracks/sync", "self._club_lock"],
    }
    for rel, tanda in perlu.items():
        f = ROOT / rel
        if not f.is_file():
            print(f"  HILANG  {rel}")
            ok = False
            continue
        t = f.read_text(encoding="utf-8", errors="replace")
        kurang = [x for x in tanda if x not in t]
        if kurang:
            print(f"  GAGAL   {rel:28s} belum memuat: {', '.join(kurang)}")
            ok = False

    js = ROOT / "app" / "static" / "quickrace.js"
    if js.is_file():
        t = js.read_text(encoding="utf-8", errors="replace")
        # data club tidak boleh disimpan di peramban
        for pola, sebab in (
            (r"localStorage\.setItem", "menyimpan data club di localStorage"),
            (r"sessionStorage\.", "menyimpan state di sessionStorage"),
            (r"https?://(?!127\.0\.0\.1|localhost)", "memanggil alamat internet"),
        ):
            if _re.search(pola, t):
                print(f"  GAGAL   quickrace.js {sebab}")
                ok = False
        # kelas CSS harus benar-benar ada (baris log pernah memakai .pline
        # yang tidak punya gaya -> teks berdesakan)
        html = (ROOT / "app" / "static" / "quickrace.html").read_text(
            encoding="utf-8", errors="replace")
        for kelas in ("pline",):
            if f'class="{kelas}"' in t or f"class=\"{kelas}" in t:
                print(f"  GAGAL   quickrace.js memakai kelas CSS .{kelas} yang tidak ada")
                ok = False
        # nilai halaman Decoder harus dibaca dari /api/state, bukan ditulis tetap
        if "segarkanDecoder" not in t:
            print("  GAGAL   halaman Decoder tidak membaca keadaan mesin (/api/state)")
            ok = False
        # gain harus bisa diatur dari layar, bukan hanya ditampilkan
        if "gainRange" not in html or "/api/decoder/gain" not in t:
            print("  GAGAL   pengatur gain tidak tersambung ke /api/decoder/gain")
            ok = False
        # membuka ulang aplikasi di tengah balapan tidak boleh memaksa SIAP
        if "setInterval(refreshRace" not in t or "Jangan menimpa keadaan sesi" not in t:
            print("  GAGAL   init memaksa status SIAP walau sesi masih berjalan")
            ok = False
        # Tombol Unggah hanya menjadwalkan antrean; server mengonfirmasi sukses.
        if '"queued": True' not in (ROOT / "app" / "webapp.py").read_text(
                encoding="utf-8", errors="replace"):
            print("  GAGAL   kontrak antrean unggah hilang dari app/webapp.py")
            ok = False
    if ok:
        print("  ok      aplikasi Quick Race offline (SQLite + API lokal, tanpa localStorage)")
    return ok



def check_gitignore() -> bool:
    """Arsip repo tidak boleh ikut membawa dependensi & hasil build.

    Pernah terjadi: zip paket ikut menyeret ``desktop/node_modules`` sampai
    205 MB. Untuk repo GitHub akibatnya lebih buruk (riwayat membengkak dan
    tidak bisa ditarik kembali), jadi aturannya dijaga di sini.
    """
    f = ROOT / ".gitignore"
    if not f.is_file():
        print("  HILANG  .gitignore")
        return False
    t = f.read_text(encoding="utf-8", errors="replace")
    wajib = ["node_modules/", "*.sqlite3", "__pycache__/", "build/", "dist/"]
    kurang = [w for w in wajib if w not in t]
    if kurang:
        print(f"  GAGAL   .gitignore belum mengecualikan: {', '.join(kurang)}")
        return False
    print("  ok      .gitignore menahan node_modules, database & hasil build")
    return True

def check_data_update_rules() -> bool:
    """Aturan data saat update harus tertulis DAN tidak dilanggar installer.

    Risiko nyata: pembaruan/uninstall menghapus %APPDATA%\YurLaps dan seluruh
    hasil balapan pengguna ikut hilang.
    """
    doc = ROOT / "docs" / "PRODUCTION-CHECKLIST.md"
    ok = True
    if not doc.is_file():
        return False
    teks = doc.read_text(encoding="utf-8", errors="replace")
    wajib = ["backup", "%APPDATA%", "SQLite", "DPAPI"]
    kurang = [k for k in wajib if k.lower() not in teks.lower()]
    if kurang:
        print(f"  GAGAL    aturan data/update belum membahas: {', '.join(kurang)}")
        ok = False

    iss = ROOT / "installer" / "stintlab.iss"
    if iss.is_file():
        t = iss.read_text(encoding="utf-8", errors="replace")
        # tidak boleh ada DeleteDir ke folder data
        for baris in t.splitlines():
            b = baris.strip().lower()
            if b.startswith("deletedir") and "appdata" in b.replace("{%appdata}", "appdata"):
                print(f"  GAGAL    installer menghapus folder data: {baris.strip()}")
                ok = False
    if ok:
        print("  ok       aturan data saat update tertulis & installer tidak menghapus data")
    return ok


def check_single_workflow() -> bool:
    """Hanya boleh ada satu workflow build; jalur Windows 8 sudah dihapus.

    Keputusan 2026-11: YurLaps untuk Windows 10+ saja. Job/jalur Windows 7/8/8.1
    di CI dibuang supaya tidak ada build yang menjanjikan sesuatu yang tidak
    bisa dijalankan Electron (Chromium butuh Windows 10+).
    """
    wd = ROOT / ".github" / "workflows"
    files = sorted(p.name for p in wd.glob("*.yml"))
    ok = True
    if len(files) > 1:
        print(f"  GAGAL    ada {len(files)} workflow: {', '.join(files)}")
        print("           cukup satu: build-windows.yml")
        ok = False
    wf = wd / "build-windows.yml"
    if not wf.is_file():
        print("  GAGAL    build-windows.yml tidak ada")
        return False
    teks = wf.read_text(encoding="utf-8", errors="replace")
    for kata in ("build-legacy-native", "YurLaps-Windows-Tk", "stintlab-win8"):
        if kata in teks:
            print(f"  GAGAL    build-windows.yml masih memuat '{kata}'")
            ok = False
    if ok:
        print("  ok       satu workflow build, tanpa jalur Windows 8")
    return ok


def check_patch_lf() -> bool:
    """Patch harus LF dan .gitattributes melindunginya.

    Kegagalan nyata: di runner Windows (core.autocrlf=true) checkout menjadi
    CRLF sehingga `git apply` menolak dengan "patch does not apply" dan setiap
    baris konteks tampak diakhiri karakter aneh.
    """
    ok = True
    patch = ROOT / "decoder" / "patch" / "rename-target.patch"
    if not patch.is_file():
        print("  HILANG  decoder/patch/rename-target.patch")
        return False
    data = patch.read_bytes()
    if b"\r" in data:
        print("  GAGAL    rename-target.patch berisi CR (harus LF)")
        ok = False
    if not data.endswith(b"\n"):
        print("  GAGAL    rename-target.patch tidak diakhiri baris baru")
        ok = False

    attr = ROOT / ".gitattributes"
    if not attr.is_file():
        print("  GAGAL    .gitattributes tidak ada (patch bisa berubah CRLF di Windows)")
        ok = False
    else:
        teks = attr.read_text(encoding="utf-8", errors="replace")
        if "*.patch" not in teks or "eol=lf" not in teks:
            print("  GAGAL    .gitattributes tidak memaksa *.patch eol=lf")
            ok = False

    # workflow harus mematikan autocrlf + punya cadangan penggantian langsung
    wf = (ROOT / ".github" / "workflows" / "build-windows.yml").read_text(
        encoding="utf-8", errors="replace")
    if "core.autocrlf false" not in wf:
        print("  GAGAL    workflow tidak mematikan core.autocrlf sebelum clone")
        ok = False
    if "openstint_add_decoder(YurLapsDecoder" not in wf:
        print("  GAGAL    workflow tidak punya cadangan penggantian nama target")
        ok = False

    if ok:
        print("  ok       patch LF + .gitattributes + workflow tahan CRLF")
    return ok


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
    """Inno packages win-unpacked produced by electron-builder."""
    wf = ROOT / ".github" / "workflows" / "build-windows.yml"
    text = wf.read_text(encoding="utf-8")
    npm, copy, inno = (text.find(marker) for marker in
                       ("run: npm run dist", "Copy-Item dist-electron", "ISCC.exe"))
    if not (0 <= npm < copy < inno):
        print("  GAGAL    urutan build harus Electron -> salin aplikasi -> Inno")
        return False
    print("  ok       workflow: Electron -> salin aplikasi -> Inno")
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


def check_versions() -> bool:
    code = (ROOT / "app/config.py").read_text(encoding="utf-8")
    version = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', code, re.M).group(1)
    package = json.loads((ROOT / "desktop/package.json").read_text())
    lock = json.loads((ROOT / "desktop/package-lock.json").read_text())
    versions = [package.get("version"), lock.get("version"),
                lock.get("packages", {}).get("", {}).get("version")]
    if any(value != version for value in versions):
        print(f"  GAGAL    versi mesin {version} tidak sama dengan Electron/lock: {versions}")
        return False
    if version not in (ROOT / "VERSI-PAKET.txt").read_text(encoding="utf-8"):
        print("  GAGAL    penanda paket tidak memuat versi aplikasi")
        return False
    print(f"  ok       versi mesin, Electron, lockfile, dan paket: {version}")
    return True


def main() -> int:
    print("Verifikasi versi & kelengkapan file\n" + "-" * 56)
    ok = check_required()
    ok = check_imports() and ok
    ok = check_workflow_quotes() and ok
    ok = check_package_marker() and ok
    ok = check_versions() and ok
    ok = check_workflow_tools() and ok
    ok = check_no_external_imports() and ok
    ok = check_patch_lf() and ok
    ok = check_single_workflow() and ok
    ok = check_data_update_rules() and ok
    ok = check_app_offline() and ok
    ok = check_gitignore() and ok
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
