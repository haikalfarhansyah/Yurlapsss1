"""Pembaruan aplikasi (update) untuk YurLaps.

Alurnya dibuat sesederhana mungkin untuk pengguna akhir:

1. YurLaps memeriksa *manifest* versi (JSON) saat start — hanya sekali, dan
   hanya kalau `auto_check_updates` aktif.
2. Kalau ada versi lebih baru, UI menampilkan tombol kecil "Versi baru tersedia".
3. Klik → unduh + ekstrak ke folder sementara → tombol berubah jadi
   "Restart & pasang".
4. Klik → YurLaps membuat skrip pembaruan, menutup diri, skrip menyalin file
   baru menimpa folder instalasi, lalu menjalankan YurLaps lagi.
   **Data pengguna tidak tersentuh** (ada di %APPDATA%\\YurLaps).

Format manifest (`update.json`) yang dipublikasikan di website/GitHub Release:

    {
      "version": "0.2.0",
      "notes": "Perbaikan timing + dukungan heat",
      "url": "https://hasil.klubku.id/unduh/YurLaps-windows-0.2.0.zip",
      "sha256": "…opsional…",
      "size": 23456789
    }
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

from .config import APP_NAME, APP_VERSION, Config, app_root, default_data_dir

IS_WINDOWS = os.name == "nt"

# Boleh dikosongkan; pengguna bisa mengisi URL manifest sendiri di Setelan.
DEFAULT_MANIFEST_URL = ""


# ---------------------------------------------------------------------------
# util versi
# ---------------------------------------------------------------------------
def parse_version(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", str(v))
    return tuple(int(n) for n in nums[:4]) or (0,)


def is_newer(latest: str, current: str = APP_VERSION) -> bool:
    a, b = parse_version(latest), parse_version(current)
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


# ---------------------------------------------------------------------------
# updater
# ---------------------------------------------------------------------------
class Updater:
    def __init__(self, cfg: Config, engine=None):
        self.cfg = cfg
        self.engine = engine
        self.dir = default_data_dir() / "updates"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state = "idle"          # idle | checking | available | downloading | ready | error
        self.latest: Optional[dict] = None
        self.progress = 0.0
        self.error = ""
        self._lock = threading.RLock()

    # -- informasi -----------------------------------------------------
    def info(self) -> dict:
        with self._lock:
            return {
                "current": APP_VERSION,
                "state": self.state,
                "latest": (self.latest or {}).get("version"),
                "notes": (self.latest or {}).get("notes", ""),
                "progress": round(self.progress, 1),
                "error": self.error,
                "can_update": self.state in ("available", "downloading", "ready"),
                "manifest_url": self.cfg.update_manifest_url or DEFAULT_MANIFEST_URL,
            }

    def _log(self, level: str, msg: str) -> None:
        if self.engine:
            self.engine.log(level, msg)

    # -- pemeriksaan ---------------------------------------------------
    def manifest_url(self) -> str:
        return (self.cfg.update_manifest_url or DEFAULT_MANIFEST_URL).strip()

    def check(self) -> dict:
        url = self.manifest_url()
        if not url:
            with self._lock:
                self.state = "idle"
            return self.info()
        with self._lock:
            self.state = "checking"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode("utf-8"))
            latest_version = str(data.get("version", "0"))
            with self._lock:
                self.latest = data
                if is_newer(latest_version):
                    self.state = "available"
                    self._log("info", f"Versi baru tersedia: {latest_version} "
                                      f"(sekarang {APP_VERSION})")
                else:
                    self.state = "idle"
            return self.info()
        except Exception as exc:
            with self._lock:
                self.state = "error"
                self.error = str(exc)
            return self.info()

    def check_async(self) -> None:
        threading.Thread(target=self.check, name="update-check", daemon=True).start()

    # -- unduh ---------------------------------------------------------
    def download_async(self) -> None:
        threading.Thread(target=self._download, name="update-download", daemon=True).start()

    def _download(self) -> None:
        with self._lock:
            manifest = dict(self.latest or {})
            if not manifest.get("url"):
                self.state = "error"
                self.error = "Manifest tidak memuat URL unduhan"
                return
            self.state = "downloading"
            self.progress = 0.0

        staging = self.dir / "staging"
        try:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True, exist_ok=True)
            zip_path = self.dir / "update.zip"

            req = urllib.request.Request(manifest["url"],
                                         headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"})
            with urllib.request.urlopen(req, timeout=120) as resp, open(zip_path, "wb") as fh:
                total = int(resp.headers.get("Content-Length") or manifest.get("size") or 0)
                done = 0
                while True:
                    chunk = resp.read(262_144)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    if total:
                        with self._lock:
                            self.progress = min(99.0, done * 100.0 / total)

            if manifest.get("sha256"):
                digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
                if digest.lower() != str(manifest["sha256"]).lower():
                    raise RuntimeError("Checksum tidak cocok — unduhan rusak")

            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(staging)
            zip_path.unlink(missing_ok=True)

            # kalau zip berisi satu folder root, masuk ke dalamnya
            entries = [p for p in staging.iterdir()]
            if len(entries) == 1 and entries[0].is_dir():
                inner = entries[0]
                for item in inner.iterdir():
                    shutil.move(str(item), str(staging / item.name))
                inner.rmdir()

            with self._lock:
                self.state = "ready"
                self.progress = 100.0
            self._log("ok", f"Pembaruan {manifest.get('version')} siap dipasang")
        except Exception as exc:
            with self._lock:
                self.state = "error"
                self.error = str(exc)
            self._log("error", f"Gagal menyiapkan pembaruan: {exc}")

    # -- pasang --------------------------------------------------------
    def install_dir(self) -> Path:
        """Folder tempat YurLaps.exe berada (atau root proyek saat dari source)."""
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        return app_root()

    def exe_name(self) -> str:
        return Path(sys.executable).name if getattr(sys, "frozen", False) else "YurLaps.exe"

    def _keep_zip(self) -> Optional[Path]:
        """Simpan zip pembaruan apa adanya untuk dipasang ulang lewat installer.

        Kenapa: memasang pembaruan dengan menyalin file (robocopy/copy2) membuat
        shortcut, entri "Apps & features", dan pendaftaran installer jadi tidak
        sinkron. Jalur yang paling rapi adalah build aplikasi yang sama tetapi
        dengan versi di dalam zip: installer akan mengenalinya sebagai versi
        baru lalu memperbarui seluruh paket sekaligus.
        """
        src = self.dir / "update.zip"
        if not src.exists():
            return None
        dst = self.dir / f"YurLaps-update-{self.latest.get('version') or 'baru'}.zip"
        try:
            shutil.copy2(src, dst)
            return dst
        except Exception:
            return src

    def install_command(self) -> Optional[tuple[list[str], Path]]:
        """Perintah untuk memasang pembaruan lewat Installer YurLaps (disarankan).

        Hasil: (argv, zip_path) atau None kalau installer tidak tersedia —
        pemanggil bisa jatuh ke ``apply_and_restart()``.
        """
        from .config import app_root

        zip_path = self._keep_zip()
        if zip_path is None:
            return None
        # 1) installer yang terpasang di mesin ini
        candidates = []
        local = os.environ.get("LOCALAPPDATA") or ""
        for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
            if base:
                candidates.append(Path(base) / "YurLaps" / "YurLaps-Setup.exe")
        if local:
            candidates.append(Path(local) / "Programs" / "YurLaps" / "YurLaps-Setup.exe")
        # 2) installer yang ikut dibundel di folder aplikasi
        candidates.append(app_root() / "YurLaps-Setup.exe")
        candidates.append(app_root() / "installer" / "YurLaps-Setup.exe")
        for cand in candidates:
            if cand.is_file():
                return ([str(cand), f"/UPDATEZIP={zip_path}"], zip_path)
            # catatan: installer membaca /UPDATEZIP lewat {param:UPDATEZIP}
            # (lihat installer/stintlab.iss), sehingga bentuk "/Nilai" aman.
        return None

    def apply_and_restart(self) -> tuple[bool, str]:
        """Salin file baru (lewat skrip terpisah) lalu jalankan ulang aplikasi."""
        staging = self.dir / "staging"
        if not staging.is_dir():
            return False, "Belum ada pembaruan yang diunduh"
        target = self.install_dir()

        if not getattr(sys, "frozen", False):
            return False, ("Mode source: jalankan `git pull` lalu start ulang. "
                           "Pembaruan otomatis hanya untuk versi .exe.")

        if IS_WINDOWS:
            bat = self.dir / "apply_update.bat"
            exe = target / self.exe_name()
            log = self.dir / "apply_update.log"
            bat.write_text(
                "@echo off\r\n"
                "timeout /t 2 /nobreak >nul\r\n"
                f'taskkill /IM "{self.exe_name()}" /F >nul 2>&1\r\n'
                "timeout /t 1 /nobreak >nul\r\n"
                f'robocopy "{staging}" "{target}" /E /R:2 /W:1 /NFL /NDL /NJH /NJS '
                f'/XD "{target / "data"}" /XF portable.txt > "{log}" 2>&1\r\n'
                f'start "" "{exe}"\r\n'
                'del "%~f0"\r\n',
                encoding="utf-8")
            try:
                DETACHED = 0x00000008 | 0x00000200
                subprocess.Popen(["cmd", "/c", str(bat)], close_fds=True,
                                 creationflags=DETACHED)
            except Exception as exc:
                return False, f"Gagal menjalankan pembaruan: {exc}"
            self._log("info", "Menutup aplikasi untuk memasang pembaruan …")
            return True, "Aplikasi akan menutup dan terbuka lagi dengan versi baru."

        # Linux / Mac: ganti file di tempat lalu minta pengguna start ulang
        try:
            for item in staging.iterdir():
                dest = target / item.name
                if item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest)
            shutil.rmtree(staging, ignore_errors=True)
            return True, "File diperbarui. Tutup dan jalankan ulang YurLaps."
        except Exception as exc:
            return False, f"Gagal menyalin file: {exc}"


# ---------------------------------------------------------------------------
# ciptakan versi (dipakai untuk merilis: python tools/buat_update_manifest.py)
# ---------------------------------------------------------------------------
def build_manifest(version: str, url: str, notes: str = "", zip_path: Optional[str] = None) -> dict:
    manifest = {"version": version, "url": url, "notes": notes}
    if zip_path and Path(zip_path).is_file():
        p = Path(zip_path)
        manifest["size"] = p.stat().st_size
        manifest["sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()
    return manifest
