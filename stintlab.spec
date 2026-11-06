# -*- mode: python ; coding: utf-8 -*-
"""Spesifikasi PyInstaller untuk YurLaps (Windows, one-folder).

Hasil: dist/YurLaps/YurLaps.exe  (folder bisa di-zip dan dipindah-pindah)
Jalankan:  pyinstaller --noconfirm --clean stintlab.spec
"""
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

hidden = collect_submodules("zmq") + [
    "app.main", "app.native", "app.desktop", "app.webapp", "app.cloud", "app.cloud_credentials", "app.cloud_entities",
        "app.member_sync", "app.race_upload", "app.club", "app.race_report", "app.race_results", "app.decoder", "app.online",
    "app.decoder_manager", "app.devices", "app.updater", "app.net", "app.native",
]

# Komponen pihak ketiga (lihat tools/fetch_vendor.py):
#   vendor/win/zadig.exe        -> pemasang driver WinUSB (libwdi, GPL-3.0)
#   vendor/openstint/*.exe      -> decoder OpenStint (opsional, bisa unduh sendiri)
datas = [("app/static", "app/static"), ("assets", "assets")]
for extra in ("vendor/win", "vendor/openstint"):
    if os.path.isdir(extra):
        datas.append((extra, extra))
# Tkinter DIPAKAI (UI native); pywebview/pythonnet tetap tidak dibundel.
# PENTING: pywebview + pythonnet (clr) TIDAK dibundel.
# Alasannya: pythonnet di dalam PyInstaller sering gagal dengan
#   "Failed to resolve Python.Runtime.Loader.Initialize ... Python.Runtime.dll"
# sehingga aplikasi mati saat dibuka. YurLaps memakai jendela aplikasi lewat
# Edge/Chrome mode --app (lihat app/desktop.py) yang jauh lebih tahan banting.
# pywebview tetap bisa dipakai saat menjalankan dari source: python -m app --desktop

a = Analysis(
    ["run_stintlab.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PIL", "pytest", "unittest",
              # jangan pernah ikutkan (sumber crash di .exe):
              "webview", "clr", "pythonnet", "clr_loader", "System"],
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
    console=False,          # ganti True kalau mau melihat log di konsol
    disable_windowed_traceback=False,
    icon=("assets/yurlaps.ico" if sys.platform == "win32" else None),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="YurLaps",
)
