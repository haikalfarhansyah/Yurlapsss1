# -*- mode: python ; coding: utf-8 -*-
"""Spec PyInstaller untuk MESIN YurLaps (dipakai jendela Electron).

Beda dengan stintlab.spec (yang membangun aplikasi Tk lengkap):
  * tanpa Tkinter  -> tidak ada UI yang digambar proses ini
  * console=True   -> stdout dipakai untuk memberi tahu port ke Electron
  * nama YurLapsEngine.exe, ditaruh di desktop/engine/ lalu dibundel
    electron-builder ke resources/engine/

Build:  python -m PyInstaller tools/engine.spec
"""
from pathlib import Path

ROOT = Path(SPECPATH).resolve()          # .../tools
PROJECT = ROOT.parent                    # .../stintlab
ASSETS = PROJECT / "assets"
OUT = PROJECT / "desktop" / "engine"

# --- berkas data yang ikut dibundel -----------------------------------------
def _datas():
    """UI web + aset + decoder/driver (vendor).

    Decoder dan alat driver ditaruh di "vendor/..." supaya saat dibundel berada
    di _internal/vendor/... — persis lokasi yang dicari app/decoder_manager.py
    (app_root() menunjuk folder _internal saat dibundel).
    """
    items = [
        (str(PROJECT / "app" / "static"), "app/static"),
        (str(ASSETS / "yurlaps-logo.png"), "assets"),
    ]
    for extra in ("vendor/win", "vendor/openstint"):
        src = PROJECT / extra
        if src.is_dir():
            items.append((str(src), extra))
    return items


a = Analysis(
    [str(ROOT / "engine_entry.py")],
    pathex=[str(PROJECT)],
    binaries=[],
    # UI web (index.html + aset) HARUS ikut: jendela Electron memuatnya dari
    # server lokal. Tanpa ini aplikasi hasil build menampilkan 404.
    datas=_datas(),
    hiddenimports=[
        "app.webapp", "app.cloud", "app.cloud_credentials", "app.cloud_entities",
        "app.member_sync", "app.race_upload", "app.club", "app.race_report", "app.race_results",
        "app.schedule",         # mesin jadwal (dipakai app.events)
        "app.schedule_report",  # PDF jadwal, diimpor di dalam fungsi
        "app.native",          # diimpor oleh modul main walau tidak dipakai
        "app.devices",
        "app.updater",
        "zmq",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Jendela aplikasi ditangani Electron: Tk, webview, pythonnet dibuang.
    excludes=[
        "tkinter", "_tkinter",
        "webview", "clr", "pythonnet", "clr_loader", "System",
        "matplotlib", "numpy", "pandas", "scipy",
        "test", "unittest",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="YurLapsEngine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="YurLapsEngine",
)

# Salin hasil ke tempat yang dicari electron-builder (extraResources: engine/)
import shutil  # noqa: E402

target = OUT
if target.exists():
    shutil.rmtree(target)
shutil.copytree(Path(DISTPATH) / "YurLapsEngine", target)
print(f"[engine] mesin disalin ke {target}")
