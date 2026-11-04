"""Supervisor decoder: menjalankan openstint.exe di latar belakang & auto-reconnect.

Perilaku yang diinginkan (dan diimplementasikan di sini):

* YurLaps **selalu terbuka** walau SDR belum terpasang / decoder belum ada.
* Decoder dijalankan sebagai proses latar belakang **tanpa jendela hitam**
  (CREATE_NO_WINDOW di Windows).
* Kalau decoder mati (YurLaps Decoder terputus, crash, USB reset) → dijalankan ulang
  otomatis dengan **backoff** (2 s, 4 s, 8 s … maksimal 30 s).
* Kalau proses masih hidup tapi tidak ada data ZeroMQ selama N detik
  (YurLaps Decoder macet / terputus saat berjalan) → proses di-restart.
* Kalau file decoder belum ada dan `decoder_auto_download` aktif → unduh rilis
  resmi OpenStint dari GitHub ke folder `bin/`.
* Status lengkap (proses hidup? berapa kali restart? kenapa gagal?) tersedia
  untuk UI lewat `status()`.
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .config import Config, app_root, default_data_dir

IS_WINDOWS = os.name == "nt"

# Rilis resmi OpenStint (Windows x64). Bisa diganti lewat config.decoder_exe.
OPENSTINT_RELEASE_API = "https://api.github.com/repos/zsellera/openstint/releases/latest"
OPENSTINT_ASSET_HINT = "windows-x64-Release.zip"

# Nama file yang kita cari setelah ekstrak (rilis resmi memakai .exe; build
# Linux tanpa ekstensi).
EXE_NAMES = ("openstint_rtlsdr.exe", "openstint_rtlsdr",
             "openstint_hackrf.exe", "openstint_hackrf")


def exe_names() -> tuple[str, ...]:
    """Urutan pencarian biner sesuai platform (di Linux/Mac tanpa .exe)."""
    if IS_WINDOWS:
        return EXE_NAMES
    return tuple(n for n in EXE_NAMES if not n.endswith(".exe")) + \
           tuple(n for n in EXE_NAMES if n.endswith(".exe"))


def command_for(exe: Path) -> list[str]:
    """Perintah untuk menjalankan biner decoder.

    Biner asli dijalankan langsung. Kalau file-nya **skrip** (diawali `#!`,
    mis. decoder hasil kompilasi sendiri atau skrip pembungkus python), ia
    dijalankan lewat interpreter-nya — karena Windows tidak mengenal shebang
    dan tidak bisa mengeksekusi file tanpa ekstensi.
    """
    exe = Path(exe)
    try:
        with open(exe, "rb") as fh:
            if fh.read(2) == b"#!":
                with open(exe, "r", encoding="utf-8", errors="replace") as sh:
                    line = sh.readline().strip()
                parts = line[2:].split()
                if parts:
                    # `#!python`, `#!/usr/bin/env python3`, `#!/usr/bin/python3`
                    if any("python" in part.lower() for part in parts):
                        return [sys.executable, str(exe)]
                    return parts + [str(exe)]
    except Exception:
        pass
    if exe.suffix.lower() == ".py":
        return [sys.executable, str(exe)]
    return [str(exe)]


@dataclass
class DecoderStatus:
    mode: str = "managed"          # managed | external | off
    running: bool = False
    pid: Optional[int] = None
    exe: str = ""
    started_at: Optional[float] = None
    restarts: int = 0
    last_exit: Optional[str] = None
    last_error: str = ""
    state: str = "idle"            # idle | starting | running | error | downloading
    message: str = ""
    args: list = field(default_factory=list)   # argumen aktif (ditampilkan di UI)
    gain: Optional[float] = None               # gain tuner aktif (dB)
    freq_hz: Optional[float] = None            # frekuensi center dari stderr decoder

    def to_dict(self) -> dict:
        return {
            "mode": self.mode, "running": self.running, "pid": self.pid,
            "exe": self.exe, "started_at": self.started_at,
            "restarts": self.restarts, "last_exit": self.last_exit,
            "last_error": self.last_error, "state": self.state,
            "message": self.message,
            "args": list(self.args or []),
            "gain": self.gain,
            "freq_hz": self.freq_hz,
        }


class DecoderManager(threading.Thread):
    def __init__(self, cfg: Config, engine, on_change=None):
        super().__init__(name="decoder-manager", daemon=True)
        self.cfg = cfg
        self.engine = engine
        self.on_change = on_change
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._proc: Optional[subprocess.Popen] = None
        self.status = DecoderStatus()
        self._backoff = 2.0
        self._log_path = default_data_dir() / "decoder.log"
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._download_thread: Optional[threading.Thread] = None
        # debounce untuk perubahan setelan (gain) supaya tidak restart beruntun
        self._restart_timer: Optional[threading.Timer] = None
        self._restart_lock = threading.Lock()
        self._wanted_stop = False       # True kalau restart memang diminta UI

    # ------------------------------------------------------------------
    def stop(self) -> None:
        self._stop.set()
        self._kill_process()

    # ------------------------------------------------------------------
    def bin_dir(self) -> Path:
        d = default_data_dir() / "bin"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def find_decoder(self) -> Optional[Path]:
        """Cari biner decoder: config → vendor → bin data dir → PATH."""
        if self.cfg.decoder_exe:
            p = Path(self.cfg.decoder_exe).expanduser()
            if p.is_file():
                return p
        for base in (app_root() / "vendor" / "openstint", self.bin_dir()):
            for name in exe_names():
                p = base / name
                if p.is_file():
                    return p
        for name in exe_names():
            found = shutil.which(name)
            if found:
                return Path(found)
        return None

    # ------------------------------------------------------------------
    def _publish(self, **kwargs) -> None:
        for k, v in kwargs.items():
            setattr(self.status, k, v)
        if self.on_change:
            try:
                self.on_change(self.status.to_dict())
            except Exception:
                pass

    def _kill_process(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
        if not proc:
            return
        try:
            if IS_WINDOWS:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True,
                               creationflags=0x08000000)
            else:
                proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    # ------------------------------------------------------------------
    def _start_process(self, exe: Path) -> bool:
        try:
            args = self.cfg.effective_decoder_args()
        except Exception:
            args = (self.cfg.decoder_args or "").split()
        cmd = command_for(exe) + args
        env = os.environ.copy()
        # rtl-sdr/libwdi di Windows butuh path driver bawaan; biarkan default.
        try:
            with open(self._log_path, "ab") as log:
                log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} mulai: "
                          f"{' '.join(cmd)}\n".encode())
                log.flush()
                flags = 0
                if IS_WINDOWS:
                    flags = 0x08000000 | 0x00000200   # NO_WINDOW | NEW_PROCESS_GROUP
                proc = subprocess.Popen(
                    cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                    cwd=str(exe.parent), env=env, creationflags=flags,
                )
        except FileNotFoundError:
            self._publish(state="error", last_error="Biner decoder tidak ditemukan",
                          message="File decoder hilang setelah diperiksa.")
            return False
        except OSError as exc:
            msg = str(exc)
            if "Exec format error" in msg or "not a valid Win32" in msg:
                msg = ("Biner decoder tidak cocok dengan sistem operasi ini "
                       "(file .exe Windows dijalankan di Linux/Mac).")
            self._publish(state="error", last_error=msg, message=msg)
            self.engine.log("error", msg)
            return False
        except Exception as exc:
            self._publish(state="error", last_error=str(exc),
                          message=f"Gagal menjalankan decoder: {exc}")
            return False

        with self._lock:
            self._proc = proc
        self._publish(running=True, pid=proc.pid, exe=str(exe), started_at=time.time(),
                      state="starting", last_error="", args=args,
                      gain=float(self.cfg.decoder_gain or 0) or None,
                      message=f"Decoder dijalankan (PID {proc.pid})")
        self.engine.log("info", f"Decoder dijalankan di latar belakang: {exe.name}"
                                + (f" ({' '.join(args)})" if args else ""))
        threading.Thread(target=self._wait_radio_line, daemon=True).start()
        return True

    def _waiting_for_dongle(self) -> bool:
        """Proses decoder hidup tapi belum/tidak lagi membuka perangkatnya.

        Dipakai untuk mempercepat percobaan ulang: kalau perangkat terputus,
        openstint akan mengeluh "no supported devices" dan tetap hidup tanpa
        data. Tanda paling andal: tidak ada baris radio (freq) dan tidak ada
        data ZMQ, plus log terakhir memuat keluhan perangkat.
        """
        if self.engine.decoder_online:
            return False        # ada data ZMQ = perangkat pasti sudah terbuka
        if self.status.freq_hz:
            return False        # perangkat terbuka & tuner diset = cuma diam, bukan isu perangkat
        tail = (self._tail_log() or "").lower()
        hints = ("no supported devices", "usb_open error", "failed to open",
                 "no device", "cannot open", "device not found",
                 "resource busy", "usb_claim_interface", "no tuner")
        if any(h in tail for h in hints):
            return True
        # proses baru hidup sebentar, belum sempat melaporkan apa pun → beri waktu
        if time.time() - (self.status.started_at or 0) < 2.5:
            return False
        # tidak ada baris radio sama sekali setelah 2,5 s → perangkat belum terbaca
        return True

    def _wait_radio_line(self, tries: int = 30) -> None:
        for _ in range(tries):
            if self._stop.wait(0.5):
                return
            self._scan_log_for_radio()
            if self.status.freq_hz:
                return

    # ------------------------------------------------------------------
    def _scan_log_for_radio(self) -> None:
        """Ambil frekuensi/gain dari baris startup decoder (stderr -> log)."""
        try:
            with open(self._log_path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - 8000))
                text = fh.read().decode("utf-8", "replace")
        except Exception:
            return
        freq = None
        for line in text.splitlines()[-40:]:
            i = line.find("RTL-SDR RX: freq=")
            if i >= 0:
                try:
                    freq = float(line[i + len("RTL-SDR RX: freq="):].split()[0])
                except (ValueError, IndexError):
                    pass
        if freq is not None and freq != self.status.freq_hz:
            self._publish(freq_hz=freq)

    def _tail_log(self, n: int = 4) -> str:
        try:
            with open(self._log_path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - 4000))
                lines = fh.read().decode("utf-8", "replace").strip().splitlines()
            return " | ".join(l.strip() for l in lines[-n:] if l.strip())
        except Exception:
            return ""

    # ------------------------------------------------------------------
    def run(self) -> None:
        if not self.cfg.manage_decoder:
            self._publish(mode="off", state="idle",
                          message="Manajemen decoder dimatikan (pakai decoder sendiri).")
            return

        self._publish(mode="managed", state="starting",
                      message="Mencari biner decoder OpenStint …")

        while not self._stop.is_set():
            exe = self.find_decoder()

            if exe is None:
                if self.cfg.decoder_auto_download and self.auto_download_supported:
                    self._ensure_download()
                    # tunggu sebentar, unduhan berjalan di thread terpisah
                    self._publish(state="downloading",
                                  message="Mengunduh YurLaps Decoder …")
                    self._stop.wait(5)
                    continue
                hint = ("Letakkan biner YurLaps Decoder di folder bin/ atau "
                        "aktifkan unduh otomatis." if self.auto_download_supported
                        else "Rilis resmi OpenStint hanya menyediakan biner Windows. "
                             "Di sistem ini, kompilasi sendiri lalu letakkan "
                             "binernya di folder bin/.")
                self._publish(state="error", running=False,
                              message="Decoder belum tersedia. " + hint)
                self._stop.wait(10)
                continue

            if not self._proc or self._proc.poll() is not None:
                if not self._start_process(exe):
                    # jangan berputar cepat kalau masalahnya bukan sementara
                    fatal = any(k in (self.status.last_error or "")
                                for k in ("tidak cocok", "tidak ditemukan", "hilang"))
                    self._stop.wait(30 if fatal else min(self._backoff, 10))
                    continue
                self._backoff = 2.0

            # pantau proses + aliran data
            started = time.time()
            while not self._stop.is_set():
                proc = self._proc
                if proc is None:
                    break
                code = proc.poll()
                if code is not None:
                    tail = self._tail_log()
                    if self._wanted_stop:
                        # restart yang memang kita minta (ubah gain) bukan crash
                        break
                    self.status.restarts += 1
                    no_dongle = self._waiting_for_dongle()
                    self._publish(
                        running=False, pid=None,
                        state="waiting" if no_dongle else "error",
                        last_exit=f"keluar dengan kode {code}",
                        message=("Menunggu YurLaps Decoder dicolokkan …"
                                 if no_dongle else
                                 f"Decoder berhenti (kode {code}). "
                                 f"Mencoba lagi dalam {int(self._backoff)} s."))
                    self.engine.log("warn", f"Decoder berhenti (kode {code}); "
                                            f"restart dalam {int(self._backoff)} s")
                    if tail:
                        self.engine.log("warn", f"Log decoder: {tail}")
                    self._stop.wait(self._backoff)
                    self._backoff = min(self._backoff * 2,
                                        float(self.cfg.decoder_restart_max_delay or 30))
                    break

                # tidak ada data ZMQ padahal proses hidup → anggap macet
                idle_limit = float(self.cfg.decoder_idle_restart or 15)
                if self._waiting_for_dongle():
                    # Perangkat belum tersambung / belum terbaca: jangan tunggu 15 s,
                    # cek tiap ~1,5 s supaya begitu dicolok langsung tersambung.
                    dongle_wait = max(1.5, float(idle_limit))
                    if time.time() - started > dongle_wait:
                        if self.status.state != "waiting":
                            self._publish(state="waiting", running=False, pid=None,
                                          message="Menunggu YurLaps Decoder terhubung …")
                            self.engine.log("info", "Decoder: menunggu YurLaps Decoder "
                                                    "terhubung")
                        self._kill_process()
                        self.status.restarts += 1     # percobaan sambung ulang
                        self._backoff = 1.5
                        break
                    self._stop.wait(0.5)
                    continue
                if time.time() - started > idle_limit:
                    last = self.engine.mono() - self.engine.decoder_last_msg
                    if last > idle_limit and not self.engine.decoder_online:
                        self.engine.log("warn", f"Decoder tidak mengirim data "
                                                f"{int(last)} s — restart")
                        self._publish(state="error",
                                      message="Decoder macet (tidak ada data). "
                                              "Me-restart …")
                        self._kill_process()
                        self.status.restarts += 1
                        break
                if self.engine.decoder_online and self.status.state != "running":
                    self._publish(state="running",
                                  message="Decoder aktif dan mengirim data.")
                    self._backoff = 2.0
                self._stop.wait(1.0)

        self._kill_process()
        self._publish(running=False, state="idle", message="Manajemen decoder berhenti.")

    # ------------------------------------------------------------------
    # unduh otomatis
    # ------------------------------------------------------------------
    @property
    def auto_download_supported(self) -> bool:
        return IS_WINDOWS

    def _ensure_download(self) -> None:
        if self._download_thread and self._download_thread.is_alive():
            return
        self._download_thread = threading.Thread(target=self._download_decoder,
                                                 name="decoder-download", daemon=True)
        self._download_thread.start()

    def _download_decoder(self) -> None:
        target_dir = self.bin_dir()
        try:
            api = urllib.request.Request(OPENSTINT_RELEASE_API,
                                         headers={"User-Agent": "YurLaps"})
            import json
            with urllib.request.urlopen(api, timeout=20) as r:
                release = json.loads(r.read().decode())
            asset = next((a for a in release.get("assets", [])
                          if OPENSTINT_ASSET_HINT in a.get("name", "")), None)
            if not asset:
                raise RuntimeError("Aset rilis Windows tidak ditemukan di GitHub")
            url = asset["browser_download_url"]
            self.engine.log("info", f"Mengunduh decoder: {asset['name']} "
                                    f"({asset['size']//1024//1024} MB)")
            tmp = target_dir / "openstint-download.zip"
            with urllib.request.urlopen(urllib.request.Request(
                    url, headers={"User-Agent": "YurLaps"}), timeout=120) as resp, \
                    open(tmp, "wb") as fh:
                shutil.copyfileobj(resp, fh)
            with zipfile.ZipFile(tmp) as zf:
                zf.extractall(target_dir)
            tmp.unlink(missing_ok=True)

            # rapikan: pindahkan biner ke akar bin/ dan beri izin eksekusi
            for name in EXE_NAMES:
                for found in target_dir.rglob(name):
                    if found.parent != target_dir:
                        shutil.move(str(found), str(target_dir / name))
                    if not IS_WINDOWS:
                        os.chmod(target_dir / name, 0o755)
            self.engine.log("ok", "Decoder OpenStint siap dipakai")
            self._publish(state="idle", message="Decoder siap dijalankan.")
        except Exception as exc:
            self._publish(state="error",
                          last_error=str(exc),
                          message=f"Gagal mengunduh decoder: {exc}")
            self.engine.log("error", f"Gagal mengunduh decoder: {exc}")

    # ------------------------------------------------------------------
    def probe(self, timeout: int = 12) -> dict:
        """Jalankan decoder sekali secara manual untuk melihat apakah YurLaps Decoder
        bisa dibuka. Berguna setelah memasang driver / menyambungkan decoder.

        Kembalikan: ok, output (log), diagnosis.
        """
        exe = self.find_decoder()
        if exe is None:
            return {"ok": False, "available": False,
                    "message": "Biner decoder belum ada. Aktifkan unduh otomatis "
                               "atau letakkan biner YurLaps Decoder di folder bin/.",
                    "output": ""}
        args = (self.cfg.decoder_args or "").split()
        cmd = command_for(exe) + args
        flags = 0
        if IS_WINDOWS:
            flags = 0x08000000
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    cwd=str(exe.parent), creationflags=flags, text=True)
        except Exception as exc:
            return {"ok": False, "message": f"Gagal menjalankan decoder: {exc}",
                    "output": ""}
        try:
            out, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                out, _ = proc.communicate(timeout=5)
            except Exception:
                out = ""
        out = (out or "").strip()
        low = out.lower()

        if any(k in low for k in ("no supported devices", "usb_open error",
                                  "failed to open", "no device", "cannot open",
                                  "device not found")):
            diagnosis, ok = ("YurLaps Decoder tidak bisa dibuka. Biasanya karena driver perangkat belum "
                             "WinUSB, YurLaps Decoder belum tersambung, atau sedang dipakai "
                             "program lain (SDR#, gqrx, Zadig)."), False
        elif any(k in low for k in ("driver", "winusb", "libusb_init")):
            diagnosis, ok = ("Masalah driver/driver library. Pasang WinUSB lewat "
                             "tombol Pasang driver, lalu uji lagi."), False
        elif proc.returncode not in (0, None) and not out:
            diagnosis, ok = (f"Decoder keluar tanpa pesan (kode {proc.returncode}). "
                             f"Coba jalankan manual untuk melihat detail."), False
        else:
            diagnosis, ok = ("Decoder berhasil terhubung ke YurLaps Decoder. Kalau lap masih belum "
                             "terbaca, periksa loop/antena dan gain."), True
        return {"ok": ok, "message": diagnosis, "output": out[-3000:],
                "returncode": proc.returncode, "exe": str(exe)}

    # ------------------------------------------------------------------
    def restart_now(self, reason: str = "diminta dari UI") -> None:
        """Dipanggil dari UI: paksa restart decoder (mis. setelah ubah gain)."""
        self.engine.log("info", f"Restart decoder ({reason})")
        self._wanted_stop = True
        with self._restart_lock:
            self._kill_process()
            self._wanted_stop = False
        self._backoff = 1.0
        self._publish(state="starting",
                      message=f"Decoder dijalankan ulang ({reason}) …")

    def restart_later(self, delay: float = 1.2, reason: str = "setelan diubah") -> None:
        """Debounce: beberapa perubahan cepat (slider gain) = satu restart."""
        with self._restart_lock:
            if self._restart_timer is not None:
                try:
                    self._restart_timer.cancel()
                except Exception:
                    pass
            t = threading.Timer(delay, self.restart_now, kwargs={"reason": reason})
            t.daemon = True
            self._restart_timer = t
            self._publish(state="starting",
                          message=f"Perubahan {reason} — decoder akan dijalankan ulang …")
            t.start()

    def apply_gain(self, gain: float, delay: float = 1.2) -> float:
        """Simpan gain baru lalu restart decoder (kalau dikelola YurLaps)."""
        try:
            gain = max(0.0, min(49.6, float(gain)))
        except (TypeError, ValueError):
            gain = 20.0
        self.cfg.decoder_gain = gain
        try:
            self.cfg.save()
        except Exception:
            pass
        self._publish(gain=gain)
        if self.cfg.manage_decoder and self.status.running:
            self.restart_later(delay, reason=f"gain {gain:g} dB")
        return gain
