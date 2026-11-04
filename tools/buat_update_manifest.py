"""Buat file update.json untuk fitur pembaruan otomatis YurLaps.

Contoh pakai (setelah installer/zip dibangun):

    python tools/buat_update_manifest.py --dir dist --base-url https://hasil.klubku.id/unduh

Hasil: dist/update.json

    {"version": "0.2.5", "notes": "...", "url": ".../YurLaps-portable.zip",
     "size": 23456789, "sha256": "..."}

Lalu di YurLaps: Setelan > Pembaruan > URL manifest > arahkan ke update.json.
Semua keluaran skrip ini ASCII supaya aman di konsol Windows (cp1252).
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:                     # konsol Windows tidak bisa mencetak karakter khusus
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

from app.config import APP_VERSION                    # noqa: E402
from app.updater import build_manifest                 # noqa: E402


def find_asset(dist: Path) -> Path | None:
    """Cari zip portable: di dist/, di root proyek, atau di cwd."""
    patterns = ("YurLaps-portable.zip", "YurLaps-windows.zip", "*.zip")
    search_dirs = [dist, dist.parent, Path.cwd(), ROOT]
    for folder in search_dirs:
        for pattern in patterns:
            hits = sorted(glob.glob(str(folder / pattern)))
            hits = [h for h in hits if not h.endswith("update.zip")]
            if hits:
                return Path(hits[0])
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="dist", help="folder hasil build")
    ap.add_argument("--version", default="", help="versi rilis (default: dari kode)")
    ap.add_argument("--base-url", default="",
                    help="URL folder tempat zip diunggah, mis. https://klub.id/unduh")
    ap.add_argument("--notes", default="", help="catatan rilis")
    ap.add_argument("--out", default="dist/update.json")
    args = ap.parse_args()

    dist = Path(args.dir)
    version = args.version or APP_VERSION
    asset = find_asset(dist)

    if asset is None:
        print("[!] Tidak ada file zip di %s (atau root proyek)." % dist)
        print("    Manifest tetap dibuat, tetapi tanpa url/size/sha256.")
        print("    Pastikan langkah 'Zip versi portable' berhasil lebih dulu.")
    else:
        print("[i] Sumber pembaruan: %s (%.1f MB)"
              % (asset.name, asset.stat().st_size / 1048576))

    url = ""
    if asset is not None and args.base_url.strip():
        url = "%s/%s" % (args.base_url.rstrip("/"), asset.name)
    elif asset is not None:
        print("[!] --base-url belum diisi: field 'url' dibiarkan kosong.")
        print("    Isi manual di update.json sebelum diunggah, atau jalankan ulang")
        print("    dengan --base-url https://domain-kamu/folder.")

    manifest = build_manifest(version, url, args.notes,
                              str(asset) if asset is not None else None)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    print("\n-> %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
