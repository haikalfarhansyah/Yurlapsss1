"""Unduh komponen pihak ketiga ke folder `vendor/` (dipakai saat build).

Yang diunduh:

1. **Zadig** (driver installer WinUSB, libwdi — GPL-3.0) ke `vendor/win/`.
   Dipakai YurLaps sebagai *fallback* bila `wdi-simple.exe` tidak tersedia.
   Diunduh dari rilis resmi GitHub pbatard/libwdi, tidak dimodifikasi.
2. **OpenStint** (Windows x64, rilis resmi) ke `vendor/openstint/` — opsional.
   Kalau tidak dibundel, YurLaps mengunduhnya sendiri saat pertama dijalankan.

Contoh:

    python tools/fetch_vendor.py                # zadig + openstint
    python tools/fetch_vendor.py --zadig        # hanya zadig
    python tools/fetch_vendor.py --openstint    # hanya openstint
    python tools/fetch_vendor.py --if-windows   # lewati kalau bukan Windows

Semua kegagalan bersifat *non-fatal*: aplikasi tetap bisa dibangun dan tetap
jalan (hanya tanpa driver-installer bawaan / tanpa biner decoder bawaan).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

try:                     # konsol Windows (cp1252) tidak bisa cetak karakter khusus
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor"

ZADIG_API = "https://api.github.com/repos/pbatard/libwdi/releases/latest"
ZADIG_ASSET = "zadig"
OPENSTINT_API = "https://api.github.com/repos/zsellera/openstint/releases/latest"
OPENSTINT_ASSET = "windows-x64-Release.zip"


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "YurLaps-build"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "YurLaps-build"})
    with urllib.request.urlopen(req, timeout=180) as r, open(dest, "wb") as fh:
        shutil.copyfileobj(r, fh)
    return dest


def fetch_zadig() -> bool:
    out_dir = VENDOR / "win"
    target = out_dir / "zadig.exe"
    if target.is_file():
        print(f"  zadig sudah ada: {target.relative_to(ROOT)}")
        return True
    try:
        rel = _get_json(ZADIG_API)
        asset = next((a for a in rel.get("assets", []) if ZADIG_ASSET in a["name"]), None)
        if not asset:
            print("  ! aset zadig tidak ditemukan di rilis libwdi")
            return False
        tmp = out_dir / "zadig-download.exe"
        _download(asset["browser_download_url"], tmp)
        tmp.replace(target)
        print(f"  + zadig {asset['name']} -> {target.relative_to(ROOT)} "
              f"({asset['size'] // 1024 // 1024} MB)")
        (out_dir / "LICENSE-libwdi.txt").write_text(
            "Zadig / libwdi — https://github.com/pbatard/libwdi\n"
            "Lisensi: GPL-3.0 (aplikasi Zadig) / BSD-3-Clause (library libwdi).\n"
            "Teks lisensi: https://github.com/pbatard/libwdi/blob/master/LICENSE\n"
            "Berkas ini tidak dimodifikasi; diunduh dari rilis resmi GitHub.\n"
            "Sumber: https://github.com/pbatard/libwdi\n", encoding="utf-8")
        return True
    except Exception as exc:
        print(f"  ! gagal mengunduh zadig: {exc}")
        return False


def fetch_openstint() -> bool:
    out_dir = VENDOR / "openstint"
    if any((out_dir / n).is_file() for n in ("openstint_rtlsdr.exe", "openstint_rtlsdr")):
        print("  openstint sudah ada")
        return True
    try:
        rel = _get_json(OPENSTINT_API)
        asset = next((a for a in rel.get("assets", []) if OPENSTINT_ASSET in a["name"]), None)
        if not asset:
            print("  ! aset openstint Windows tidak ditemukan")
            return False
        zip_path = _download(asset["browser_download_url"], out_dir / "openstint.zip")
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(out_dir)
        zip_path.unlink(missing_ok=True)
        for name in ("openstint_rtlsdr.exe", "openstint_rtlsdr"):
            for found in out_dir.rglob(name):
                if found.parent != out_dir:
                    shutil.move(str(found), str(out_dir / name))
        print(f"  + openstint {asset['name']} -> vendor/openstint/ "
              f"({asset['size'] // 1024 // 1024} MB)")
        (out_dir / "ATTRIBUTION.txt").write_text(
            "OpenStint - https://github.com/zsellera/openstint\n"
            "Lisensi: GNU General Public License v3.0 (GPL-3.0)\n"
            "Teks lisensi: https://github.com/zsellera/openstint/blob/master/LICENSE\n"
            "Sumber versi ini: " + rel.get("html_url", "https://github.com/zsellera/openstint/releases") + "\n\n"
            "Biner resmi dari rilis GitHub, TIDAK dimodifikasi.\n"
            "YurLaps hanya menjalankannya sebagai proses terpisah dan berkomunikasi\n"
            "lewat protokol ZeroMQ yang didokumentasikan proyek tersebut.\n"
            "YurLaps tidak berafiliasi dengan pemilik OpenStint.\n", encoding="utf-8")
        return True
    except Exception as exc:
        print(f"  ! gagal mengunduh openstint: {exc}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zadig", action="store_true")
    ap.add_argument("--openstint", action="store_true")
    ap.add_argument("--if-windows", action="store_true",
                    help="hanya jalankan bila platform Windows (untuk CI)")
    args = ap.parse_args()

    if args.if_windows and os.name != "nt":
        print("Lewati: bukan Windows")
        return 0

    do_zadig = args.zadig or not (args.zadig or args.openstint)
    do_openstint = args.openstint or not (args.zadig or args.openstint)

    print("Menyiapkan komponen pihak ketiga di vendor/ ...")
    ok = True
    if do_zadig:
        ok &= fetch_zadig()
    if do_openstint:
        ok &= fetch_openstint()
    print("Selesai." if ok else "Selesai dengan peringatan (aplikasi tetap bisa dibangun).")
    return 0          # selalu 0: build tidak boleh gagal karena ini


if __name__ == "__main__":
    sys.exit(main())
