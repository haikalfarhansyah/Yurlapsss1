"""Manajemen perangkat: deteksi YurLaps Decoder & pemasangan driver WinUSB.

Kenapa perlu?
  YurLaps Decoder di Windows butuh driver WinUSB (dipasang lewat Zadig/libwdi).
  Tanpa itu, decoder tidak bisa membuka perangkatnya. YurLaps bisa memeriksa dan
  memasangnya sendiri lewat `wdi-simple.exe` (libwdi, lisensi GPL-3.0+BSD,
  alat yang sama yang dipakai Zadig) yang ikut dibundel di folder
  `vendor/win/` (lihat tools/fetch_vendor.py).

Catatan penting:
  * Pemasangan driver butuh **hak administrator** (UAC). Di UI, tombol
    "Pasang driver" akan memunculkan dialog UAC — itu normal dan tetap
    terlihat profesional (lebih baik daripada instalasi senyap yang gagal).
  * Tanpa wdi-simple.exe, YurLaps tetap jalan: hanya tidak bisa memasang
    driver, tapi tetap bisa memakai perangkat yang drivernya sudah beres.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .config import app_root

IS_WINDOWS = os.name == "nt"


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------
@dataclass
class DeviceInfo:
    present: bool = False
    name: str = ""
    vid: Optional[str] = None
    pid: Optional[str] = None
    driver_ok: Optional[bool] = None      # None = tidak bisa dipastikan
    service: str = ""                    # "WinUSB", "RTL2832U_IR", "bulk", ...
    status: str = "unknown"              # ok | no_driver | not_plugged | unknown
    message: str = ""
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "present": self.present, "name": self.name, "vid": self.vid,
            "pid": self.pid, "driver_ok": self.driver_ok, "service": self.service,
            "status": self.status, "message": self.message, "details": self.details,
        }


@dataclass
class InstallResult:
    ok: bool
    message: str
    log: str = ""
    needs_admin: bool = False


# ---------------------------------------------------------------------------
# lokasi tool
# ---------------------------------------------------------------------------
def wdi_simple_path() -> Optional[Path]:
    """Cari wdi-simple.exe (bundled atau di PATH)."""
    candidates = [
        app_root() / "vendor" / "win" / "wdi-simple.exe",
        app_root() / "vendor" / "wdi-simple.exe",
    ]
    # saat dibundel PyInstaller, file data ada di sys._MEIPASS
    base = getattr(sys, "_MEIPASS", None)
    if base:
        candidates.insert(0, Path(base) / "vendor" / "win" / "wdi-simple.exe")
    for c in candidates:
        if c.is_file():
            return c
    found = shutil.which("wdi-simple") or shutil.which("wdi-simple.exe")
    return Path(found) if found else None


# ---------------------------------------------------------------------------
# deteksi perangkat
# ---------------------------------------------------------------------------
def _run(cmd: list[str], timeout: int = 25) -> str:
    try:
        flags = 0x08000000 if IS_WINDOWS else 0     # CREATE_NO_WINDOW
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                             creationflags=flags)
        return (out.stdout or "") + (out.stderr or "")
    except Exception as exc:
        return f"__error__ {exc}"


def detect(vid: str = "0x0bda", pid: str = "0x2838") -> DeviceInfo:
    """Cari YurLaps Decoder yang terpasang dan periksa driver-nya."""
    fake = os.environ.get("STINTLAB_FAKE_DEVICE", "").strip().lower()
    if fake:      # hanya untuk demo/screenshot: pura-pura kondisi tertentu
        if fake == "ok":
            return DeviceInfo(present=True, name="Bulk-In, Interface (Interface 0)",
                              vid=_norm_hex(vid), pid=_norm_hex(pid), driver_ok=True,
                              service="WinUSB", status="ok",
                              message="Driver WinUSB siap — YurLaps Decoder bisa dipakai.")
        if fake == "no_driver":
            return DeviceInfo(present=True, name="RTL2832U DVB-T",
                              vid=_norm_hex(vid), pid=_norm_hex(pid), driver_ok=False,
                              service="RTL2832U", status="no_driver",
                              message="Dongle memakai driver DVB-T bawaan Windows "
                                      "(RTL2832U). Perlu diganti ke WinUSB supaya "
                                      "decoder bisa membukanya.")
        return DeviceInfo(present=False, status="not_plugged",
                          message="YurLaps Decoder tidak terdeteksi. Sambungkan perangkatnya.")
    if not IS_WINDOWS:
        return _detect_posix()

    vid_hex = _norm_hex(vid)
    pid_hex = _norm_hex(pid)
    info = DeviceInfo(vid=vid_hex, pid=pid_hex)

    # 1) cara paling andal di Windows: WMI (bisa tanpa driver terpasang)
    ps = (
        "Get-CimInstance Win32_PnPEntity | "
        "Where-Object { $_.PNPDeviceID -like 'USB*' } | "
        "Select-Object Name,PNPDeviceID,Service,Status | ConvertTo-Json -Compress"
    )
    raw = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps])
    if raw.startswith("__error__"):
        return _detect_fallback(info, vid_hex, pid_hex)

    import json
    try:
        data = json.loads(raw) if raw.strip().startswith(("[", "{")) else []
    except Exception:
        data = []
    if isinstance(data, dict):
        data = [data]

    for dev in data:
        pnp = (dev.get("PNPDeviceID") or "").upper()
        if vid_hex.upper() in pnp and pid_hex.upper() in pnp:
            info.present = True
            info.name = dev.get("Name") or "YurLaps Decoder"
            info.service = dev.get("Service") or ""
            info.details = {k: v for k, v in dev.items() if k != "PNPDeviceID"}
            break

    if not info.present:
        info.status = "not_plugged"
        info.message = ("YurLaps Decoder tidak terdeteksi. Sematkan ke port USB "
                        "dan coba lagi.")
        return info

    return _classify(info)


def _detect_fallback(info: DeviceInfo, vid: str, pid: str) -> DeviceInfo:
    """Kalau PowerShell diblokir: pakai pnputil / wmic."""
    raw = _run(["pnputil", "/enum-devices", "/connected"])
    if "__error__" not in raw and vid.upper() in raw.upper():
        info.present = True
        info.name = "YurLaps Decoder (via pnputil)"
        return _classify(info)
    info.status = "unknown"
    info.message = "Tidak bisa memeriksa perangkat USB secara otomatis di sistem ini."
    return info


def _classify(info: DeviceInfo) -> DeviceInfo:
    svc = (info.service or "").lower()
    name = (info.name or "").lower()

    winusb_services = ("winusb",)
    bulk_services = ("librtlsdr", "rtl2832u_ir", "libusbk", "libusb0", "rtlsdr")
    blocked = ("rtl2832u_nir", "rtl2832u", "rtl28xx", "dvb", "realtek")

    if any(s in svc for s in winusb_services):
        info.driver_ok, info.status = True, "ok"
        info.message = "Driver WinUSB siap — YurLaps Decoder bisa dipakai."
    elif any(s in svc for s in bulk_services):
        info.driver_ok, info.status = True, "ok"
        info.message = (f"Driver alternatif terpasang ({info.service or 'bulk'}). "
                        "Biasanya tetap bisa dipakai decoder.")
    elif any(s in svc for s in blocked) or "rtl" in name and "sdr" not in name:
        info.driver_ok, info.status = False, "no_driver"
        info.message = ("YurLaps Decoder memakai driver DVB-T bawaan Windows ("
                        f"{info.service or 'Realtek'}). Perlu diganti ke WinUSB "
                        "supaya decoder bisa membukanya.")
    elif not svc:
        info.driver_ok, info.status = False, "no_driver"
        info.message = ("Belum ada driver untuk YurLaps Decoder (tanda seru kuning di "
                        "Device Manager). Pasang driver WinUSB.")
    else:
        info.driver_ok, info.status = None, "unknown"
        info.message = f"Driver terpasang: {info.service}. Belum bisa dipastikan cocok."
    return info


def _detect_posix() -> DeviceInfo:
    info = DeviceInfo()
    for dev in Path("/dev").glob("bus/usb/*/*"):
        try:
            usbid = (dev.parent.name + ":" + dev.name).lower()
        except Exception:
            continue
        if "0bda" in usbid:
            info.present = True
            info.name = "YurLaps Decoder (Linux)"
            info.driver_ok = True
            info.status = "ok"
            info.message = ("Terdeteksi di Linux. Kalau perangkat dipakai DVB-T, buat "
                            "aturan udev agar driver bawaan tidak mengambilnya.")
            return info
    info.status = "not_plugged"
    info.message = "YurLaps Decoder tidak terdeteksi di /dev/bus/usb."
    return info


def _norm_hex(v: str) -> str:
    v = (v or "").strip()
    if not v:
        return "0x0bda"
    if v.lower().startswith(("0x", "0X")):
        return "0x" + v[2:].upper().rjust(4, "0")
    return "0x" + v.upper().rjust(4, "0")


# ---------------------------------------------------------------------------
# pemasangan driver
# ---------------------------------------------------------------------------
def zadig_path() -> Optional[Path]:
    """Zadig (GUI) — fallback yang ramah pengguna kalau wdi-simple tidak ada."""
    candidates = [app_root() / "vendor" / "win" / "zadig.exe"]
    base = getattr(sys, "_MEIPASS", None)
    if base:
        candidates.insert(0, Path(base) / "vendor" / "win" / "zadig.exe")
    for c in candidates:
        if c.is_file():
            return c
    return None


def driver_tool() -> tuple[str, Optional[Path]]:
    """Kembalikan ('wdi-simple'|'zadig'|'', path)."""
    w = wdi_simple_path()
    if w:
        return "wdi-simple", w
    z = zadig_path()
    if z:
        return "zadig", z
    return "", None


def can_install_driver() -> tuple[bool, str]:
    if not IS_WINDOWS:
        return False, "Pemasangan driver otomatis hanya tersedia di Windows."
    kind, _ = driver_tool()
    if kind == "wdi-simple":
        return True, "siap"
    if kind == "zadig":
        return True, "siap (memakai Zadig)"
    return False, ("Alat pemasang driver belum dibundel. Jalankan "
                   "`python tools/fetch_vendor.py` saat build, atau pasang driver "
                   "manual dengan Zadig.")


def install_driver(vid: str = "0x0bda", pid: str = "0x2838",
                   timeout: int = 300) -> InstallResult:
    """Pasang driver WinUSB lewat wdi-simple (memunculkan UAC bila perlu)."""
    ok, why = can_install_driver()
    if not ok:
        return InstallResult(False, why)

    kind, exe = driver_tool()
    if kind == "zadig" and exe is not None:
        # Zadig adalah aplikasi GUI: buka dengan pilihan perangkat yang disaring,
        # lalu pengguna klik "Install Driver". Ini satu-satunya cara resmi
        # tanpa menyertakan sertifikat driver sendiri.
        try:
            subprocess.Popen([str(exe)], close_fds=True)
        except Exception as exc:
            return InstallResult(False, f"Zadig tidak bisa dibuka: {exc}")
        return InstallResult(
            True,
            "Zadig dibuka. Pilih YurLaps Decoder (0BDA 2838), pastikan target "
            "driver = WinUSB, lalu klik Install Driver. Setelah selesai, tekan "
            "Ulangi pemeriksaan.",
            log="launcher:zadig")
    assert exe is not None
    cmd = [
        str(exe),
        "--type", "0",                      # 0 = WinUSB
        # Satu kata, tanpa spasi: nama perangkat muncul di Device Manager/registry
        # dan dipakai ulang oleh [Run] installer yang tidak bisa memuat kutip.
        "--name", "YurLapsDecoder",
        "--manufacturer", "Realtek",
        "--vid", _norm_hex(vid),
        "--pid", _norm_hex(pid),
        "--silent",
    ]
    raw = _run(cmd, timeout=timeout)
    if raw.startswith("__error__"):
        return InstallResult(False, "Gagal menjalankan pemasang driver.", raw,
                             needs_admin=True)
    ok = "success" in raw.lower() and "error" not in raw.lower()
    msg = ("Driver WinUSB berhasil dipasang. Sambungkan ulang YurLaps Decoder, lalu "
           "decoder akan mendeteksinya." if ok else
           "Pemasangan driver gagal. Pastikan dialog UAC disetujui, lalu coba lagi.")
    return InstallResult(ok, msg, raw)


def windows_driver_help() -> list[str]:
    """Langkah manual kalau pemasangan otomatis tidak memungkinkan."""
    return [
        "Sambungkan YurLaps Decoder.",
        "Buka Device Manager → cari perangkat bertanda seru (Bulk-In, Interface 0).",
        "Klik kanan → Update driver → Browse → Let me pick → WinUSB (atau pakai Zadig).",
        "Sambungkan ulang YurLaps Decoder, lalu tekan Ulangi pemeriksaan di YurLaps.",
    ]
