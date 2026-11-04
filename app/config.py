"""Konfigurasi YurLaps.

Semua setelan disimpan sebagai JSON di folder data pengguna, sehingga file .exe
bisa diletakkan di mana saja (termasuk USB stick).
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path

APP_NAME = "YurLaps"
LEGACY_APP_NAME = "StintLab"   # folder data lama (0.2.x) → dimigrasikan otomatis
APP_VERSION = "0.3.0"


def default_data_dir() -> Path:
    """Folder data: %APPDATA%/YurLaps di Windows, ~/.yurlaps di OS lain.

    Bisa dioverride lewat env var YURLAPS_DATA (alias lama: STINTLAB_DATA),
    berguna untuk mode portable.
    """
    env = os.environ.get("YURLAPS_DATA") or os.environ.get("STINTLAB_DATA")
    if env:
        return Path(env).expanduser()
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    return Path.home() / ".yurlaps"


def migrate_legacy_data() -> None:
    """Pindahkan data dari folder lama (StintLab) ke folder baru (YurLaps).

    Nama aplikasi berubah, tapi data pengguna (config.json + .sqlite3)
    tidak boleh hilang: disalin sekali, folder lama dibiarkan utuh.
    """
    import shutil

    if os.environ.get("YURLAPS_DATA") or os.environ.get("STINTLAB_DATA"):
        return                                  # folder ditentukan sendiri
    try:
        new_dir = default_data_dir()
        old_dir = (Path(os.environ.get("APPDATA") or "") / LEGACY_APP_NAME
                   if os.name == "nt" else Path.home() / ".stintlab")
        if new_dir.exists() or not old_dir.is_dir():
            return
        new_dir.mkdir(parents=True, exist_ok=True)
        for name in ("config.json", "stintlab.sqlite3"):
            src = old_dir / name
            if src.exists():
                shutil.copy2(src, new_dir / name)
        # nama file basis data ikut berganti
        if (new_dir / "stintlab.sqlite3").exists() and \
                not (new_dir / "yurlaps.sqlite3").exists():
            (new_dir / "stintlab.sqlite3").rename(new_dir / "yurlaps.sqlite3")
    except Exception:
        pass                                    # migrasi gagal bukan alasan app mati


def app_root() -> Path:
    """Folder root aplikasi (berguna saat dibundel PyInstaller)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


@dataclass
class TransponderMapping:
    """Data personal sebuah transponder."""

    transponder_id: int
    transponder_type: str = "OPN"
    driver: str = ""
    car: str = ""
    class_name: str = ""
    note: str = ""

    @property
    def display_name(self) -> str:
        return self.driver or f"#{self.transponder_id}"


@dataclass
class Config:
    # --- decoder -----------------------------------------------------------
    zmq_endpoint: str = "tcp://127.0.0.1:5556"
    decoder_name: str = "OpenStint"

    # --- manajemen decoder (opsional, Windows) -----------------------------
    manage_decoder: bool = True        # YurLaps yang menjalankan decoder sendiri
    decoder_exe: str = ""              # kosong = cari otomatis / unduh dari GitHub
    decoder_args: str = ""             # argumen tambahan (bebas, mis. "-s data")
    decoder_gain: float = 20.0         # -g <0..40> tuner gain (dB)
    decoder_bias_tee: bool = False     # -b bias tee +4.5 V
    decoder_direct_sampling: bool = False  # -D direct sampling Q-branch
    decoder_device_serial: str = ""    # -d <serial> (kosong = dongle pertama)
    decoder_use_system_clock: bool = False  # -t pakai jam sistem
    decoder_monitor: bool = False      # -m monitor mode (cetak frame)
    decoder_auto_download: bool = True # unduh rilis resmi kalau belum ada
    decoder_restart_max_delay: int = 30  # detik, batas backoff restart
    decoder_idle_restart: int = 15     # detik tanpa data ZMQ -> restart decoder

    # --- driver SDR (opsional) --------------------------------------------
    driver_vid: str = "0x0bda"         # RTL-SDR (Realtek)
    driver_pid: str = "0x2838"
    driver_check_on_start: bool = True

    # --- pembaruan --------------------------------------------------------
    auto_check_updates: bool = True
    update_manifest_url: str = ""      # kosong = pakai repo default di updater.py

    # --- perilaku stint ----------------------------------------------------
    idle_split_seconds: int = 120     # idle -> stint ditutup, lap berikut = stint baru
    min_lap_seconds: float = 1.5      # lap lebih cepat dari ini diabaikan (ghost)
    beep_on_lap: bool = True
    announce_lap_times: bool = True   # suara "lap 12, 8 detik 45"
    beep_volume: float = 0.6

    # --- server lokal ------------------------------------------------------
    host: str = "0.0.0.0"
    port: int = 8080
    access_token: str = ""            # kosong = tanpa token (LAN tertutup)

    # --- sinkronisasi online (opsional) -----------------------------------
    online_enabled: bool = False
    online_mode: str = "push"         # "push" (kirim ke website) | "pull" (website ambil)
    online_url: str = ""              # cth: https://hasil.klubku.id
    online_token: str = ""
    online_track: str = ""
    sync_interval: float = 2.0

    # --- identitas ---------------------------------------------------------
    track_name: str = "Sirkuit Latihan"
    event_name: str = "Practice Day"

    mappings: dict[str, TransponderMapping] = field(default_factory=dict)

    # ----------------------------------------------------------------------
    def effective_decoder_args(self) -> list[str]:
        """Argumen baris perintah final untuk openstint_rtlsdr.

        Gain & opsi radio disusun dari setelan terstruktur supaya UI bisa
        mengubahnya tanpa pengguna menyunting teks argumen. `decoder_args`
        hanya untuk tambahan bebas (mis. ``-s data``).
        """
        args: list[str] = []
        try:
            gain = float(self.decoder_gain)
        except (TypeError, ValueError):
            gain = 20.0
        gain = max(0.0, min(49.6, gain))
        args += ["-g", ("%g" % gain)]
        if self.decoder_bias_tee:
            args.append("-b")
        if self.decoder_direct_sampling:
            args.append("-D")
        serial = (self.decoder_device_serial or "").strip()
        if serial:
            args += ["-d", serial]
        if self.decoder_use_system_clock:
            args.append("-t")
        if self.decoder_monitor:
            args.append("-m")
        extra = (self.decoder_args or "").strip()
        if extra:
            args += extra.split()
        return args

    # ----------------------------------------------------------------------
    @staticmethod
    def path() -> Path:
        return default_data_dir() / "config.json"

    @classmethod
    def load(cls) -> "Config":
        p = cls.path()
        if not p.exists():
            cfg = cls()
            cfg.save()
            return cfg
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return cls()
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        data = {k: v for k, v in raw.items() if k in known}
        maps = {}
        for key, val in (data.pop("mappings", None) or {}).items():
            try:
                m = TransponderMapping(
                    transponder_id=int(val.get("transponder_id", key)),
                    transponder_type=val.get("transponder_type", "OPN"),
                    driver=val.get("driver", ""),
                    car=val.get("car", ""),
                    class_name=val.get("class_name", ""),
                    note=val.get("note", ""),
                )
                maps[str(m.transponder_id)] = m
            except Exception:
                continue
        cfg = cls(**data)
        cfg.mappings = maps
        # migrasi 0.2.x: gain dulu hanya ada di string decoder_args
        raw_args = (cfg.decoder_args or "").strip()
        if raw_args:
            parts = raw_args.split()
            kept: list[str] = []
            i = 0
            while i < len(parts):
                tok = parts[i]
                if tok == "-g" and i + 1 < len(parts):
                    try:
                        cfg.decoder_gain = float(parts[i + 1])
                    except ValueError:
                        pass
                    i += 2
                    continue
                if tok.startswith("-g") and len(tok) > 2:
                    try:
                        cfg.decoder_gain = float(tok[2:])
                    except ValueError:
                        pass
                    i += 1
                    continue
                if tok in ("-b", "-D", "-t", "-m"):
                    setattr(cfg, {"-b": "decoder_bias_tee",
                                  "-D": "decoder_direct_sampling",
                                  "-t": "decoder_use_system_clock",
                                  "-m": "decoder_monitor"}[tok], True)
                    i += 1
                    continue
                if tok == "-d" and i + 1 < len(parts):
                    cfg.decoder_device_serial = parts[i + 1]
                    i += 2
                    continue
                kept.append(tok)
                i += 1
            cfg.decoder_args = " ".join(kept)
        return cfg

    def save(self) -> None:
        p = self.path()
        p.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)

    # ----------------------------------------------------------------------
    def mapping_for(self, transponder_id: int) -> TransponderMapping:
        key = str(int(transponder_id))
        m = self.mappings.get(key)
        if m is None:
            m = TransponderMapping(transponder_id=int(transponder_id))
            self.mappings[key] = m
        return m

    def driver_name(self, transponder_id: int) -> str:
        return self.mapping_for(transponder_id).display_name
