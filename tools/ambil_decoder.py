"""Ambil biner decoder YurLaps dari rilis repo decoder (build sendiri).

Dipakai CI Windows supaya YurLaps tidak lagi mengambil biner pihak ketiga:
alih-alih mengunduh dari rilis OpenStint, kita mengunduh ZIP hasil build kita
sendiri yang sudah diberi nama YurLapsDecoder.

Contoh:
    python tools/ambil_decoder.py --repo akun/yurlaps-decoder
    python tools/ambil_decoder.py --repo akun/yurlaps-decoder --tag v1.0.0
    python tools/ambil_decoder.py --file /path/ke/yurlaps-decoder-win64.zip

Hasil: vendor/openstint/  berisi YurLapsDecoder.exe + DLL + ATTRIBUTION.txt

Repositori decoder boleh PRIVATE; skrip ini memakai GITHUB_TOKEN kalau ada
(beri izin "Contents: Read" pada repo tersebut).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor" / "openstint"
ASSET_HINT = "yurlaps-decoder-win64"
API = "https://api.github.com"


def _headers() -> dict:
    h = {"User-Agent": "YurLaps-build", "Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def _download(url: str, dest: Path) -> Path:
    req = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(req, timeout=300) as r, open(dest, "wb") as fh:
        shutil.copyfileobj(r, fh)
    return dest


def find_asset(repo: str, tag: str | None) -> tuple[str, str]:
    """Kembalikan (url_unduh, versi) untuk aset ZIP decoder."""
    if tag:
        rel = _get_json(f"{API}/repos/{repo}/releases/tags/{tag}")
    else:
        try:
            rel = _get_json(f"{API}/repos/{repo}/releases/latest")
        except Exception:
            # belum ada rilis: pakai artifact-free fallback -> error jelas
            raise SystemExit(
                f"Repo {repo} belum punya rilis. Buat tag (mis. v1.0.0) lebih dulu."
            )
    version = rel.get("tag_name") or "unknown"
    for asset in rel.get("assets", []):
        if ASSET_HINT in asset["name"] and asset["name"].endswith(".zip"):
            return asset["browser_download_url"], version
    raise SystemExit(
        f"Aset '{ASSET_HINT}*.zip' tidak ada di rilis {version} dari {repo}."
    )


def install_from_zip(zip_path: Path) -> int:
    VENDOR.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = Path(info.filename).name
            if not name:
                continue
            target = VENDOR / name
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            count += 1
    return count


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", help="pemilik/nama repo decoder, mis. akun/yurlaps-decoder")
    ap.add_argument("--tag", help="tag rilis (default: rilis terbaru)")
    ap.add_argument("--file", help="pakai file ZIP lokal (tanpa internet)")
    args = ap.parse_args(argv)

    tmp = Path(tempfile.mkdtemp(prefix="yurlaps-decoder-"))
    try:
        if args.file:
            zip_path = Path(args.file)
            if not zip_path.is_file():
                raise SystemExit(f"File tidak ditemukan: {zip_path}")
            version = "lokal"
        elif args.repo:
            url, version = find_asset(args.repo, args.tag)
            print(f"  mengunduh {url}")
            zip_path = _download(url, tmp / "decoder.zip")
        else:
            raise SystemExit("Sebutkan --repo atau --file.")

        n = install_from_zip(zip_path)
        print(f"  + {n} berkas -> vendor/openstint/  (decoder {version})")

        exe = [p for p in VENDOR.iterdir()
               if p.name.lower().startswith("yurlapsdecoder")]
        if not exe:
            print("  ! YurLapsDecoder tidak ada di dalam zip", file=sys.stderr)
            return 1
        print(f"  ok: {exe[0].name}")
        if not (VENDOR / "ATTRIBUTION.txt").is_file():
            print("  ! ATTRIBUTION.txt tidak ikut di zip (wajib untuk GPL-3.0)",
                  file=sys.stderr)
            return 1
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
