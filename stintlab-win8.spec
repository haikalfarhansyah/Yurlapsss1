# -*- mode: python ; coding: utf-8 -*-
"""Spesifikasi PyInstaller untuk Windows 7 / 8 / 8.1.

Bedanya dengan stintlab.spec:
  * pywebview tidak diikutkan (WebView2 tidak didukung Windows 8.1)
  * console=True  -> jendela konsol kecil sebagai "rumah" aplikasi,
                     sekaligus tempat log decoder terlihat
  * aplikasi membuka browser default secara otomatis (mode browser)

Hasil: dist/YurLaps-win8/YurLaps.exe
Build dengan Python 3.11 (atau 3.8 untuk kompatibilitas maksimum):
    py -3.11 -m PyInstaller --noconfirm --clean stintlab-win8.spec
"""
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

hidden = collect_submodules("zmq") + [
    "app.main", "app.webapp", "app.cloud", "app.cloud_credentials", "app.cloud_entities",
        "app.member_sync", "app.race_upload", "app.club", "app.race_report", "app.race_results", "app.decoder", "app.online",
    "app.decoder_manager", "app.devices", "app.updater", "app.net", "app.native",
]

datas = [("app/static", "app/static")]
# Komponen pihak ketiga opsional (lihat tools/fetch_vendor.py)
for extra in ("vendor/win", "vendor/openstint"):
    if os.path.isdir(extra):
        datas.append((extra, extra))

a = Analysis(
    ["run_stintlab_win8.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PIL", "pytest", "unittest", "webview"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="YurLaps",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,           # konsol kecil: menampilkan alamat & log decoder
    disable_windowed_traceback=False,
    icon=("assets/yurlaps.ico" if sys.platform == "win32" else None),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="YurLaps-win8",
)
