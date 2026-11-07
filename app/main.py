"""Entry point YurLaps.

Dua cara pakai:

1. Desktop (Windows):  jalankan YurLaps.exe → UI muncul di jendela sendiri.
2. Headless/server:    python -m app.main --host 0.0.0.0 --port 8080
   → buka http://IP-komputer:8080 di HP, tablet, atau TV di LAN.
"""
from __future__ import annotations

import argparse
import os
import signal
import socket
import sys
import threading
import time
import webbrowser


def _safe_console() -> None:
    """Jangan biarkan emoji/karakter khusus membuat aplikasi crash di Windows.

    Konsol Windows (cp1252) melempar UnicodeEncodeError untuk karakter seperti
    ✅ atau —. Kita ganti karakter yang tak bisa dikodekan, bukan mati.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")      # type: ignore[union-attr]
        except Exception:
            pass


from .config import APP_NAME, APP_VERSION, Config, default_data_dir
from .decoder import DecoderClient
from .decoder_manager import DecoderManager
from .engine import Engine
from .net import lan_ips
from .online import OnlineSync
from .updater import Updater
from .storage import Storage
from .webapp import AppContext, EventBus, start_server

BANNER = r"""
  ____  _   _ ___ _   _ _____ _        _    ____
 / ___|| | | |_ _| \ | |_   _| |      / \  | __ )
 \___ \| |_| || ||  \| | | | | |     / _ \ |  _ \
  ___) |  _  || || |\  | | | | |___ / ___ \| |_) |
 |____/|_| |_|___|_| \_| |_| |_____/_/   \_\____/   v{ver}
"""


class YurLaps:
    def __init__(self, args):
        if args.data_dir:
            os.environ["YURLAPS_DATA"] = args.data_dir
        self.cfg = Config.load()
        if args.port is not None:
            self.cfg.port = args.port
        if args.host:
            self.cfg.host = args.host
        if args.endpoint:
            self.cfg.zmq_endpoint = args.endpoint

        self.bus = EventBus()
        self.storage = Storage()
        self.engine = Engine(self.cfg, storage=self.storage,
                             on_event=self.bus.publish)

        self.ctx = AppContext(self.cfg, self.engine, self.storage, self.bus)
        self.httpd = None
        self.decoder: DecoderClient | None = None
        self.supervisor: DecoderManager | None = None
        self.updater: Updater | None = None
        self.online: OnlineSync | None = None
        self._stop = threading.Event()

        # bus juga menerima log/event dari engine
        self.engine.log("info", f"{APP_NAME} v{APP_VERSION} · data: {default_data_dir()}")

    # ------------------------------------------------------------------
    def start(self) -> tuple[str, int]:
        host = self.cfg.host or "0.0.0.0"
        port = int(self.cfg.port if self.cfg.port is not None else 8080)
        self.httpd = start_server(self.ctx, host, port, block=False)
        real_port = self.httpd.server_address[1]
        self._start_engine()
        self.engine.log("ok", f"UI siap di http://localhost:{real_port}")
        if host not in ("127.0.0.1", "localhost", "::1"):
            self._hint_lan(real_port)
        return host, real_port

    def start_electron(self) -> tuple[str, int]:
        """Mesin + decoder + server HTTP lokal untuk jendela Electron.

        Server hanya mengikat 127.0.0.1 di port acak (0 = pilih otomatis),
        sehingga tidak ada dialog firewall dan tidak bisa dibuka dari
        jaringan. Kembalian: (host, port) untuk shell Electron.
        """
        self._start_engine()
        from .webapp import start_server

        self.httpd = start_server(self.ctx, "127.0.0.1", 0, block=False)
        port = self.httpd.server_address[1]
        self.engine.log("info", f"Jendela aplikasi: http://127.0.0.1:{port}/")
        return "127.0.0.1", port

    def start_native(self) -> tuple[str, int]:
        """Mesin + decoder saja, tanpa server HTTP (mode native Tkinter).

        Dipakai app/native.py: UI digambar Tk, jadi tidak perlu membuka port
        apa pun — tidak ada firewall prompt, tidak ada browser.
        """
        self._start_engine()
        self.engine.log("ok", "Mode native: UI Tkinter (tanpa server web)")
        return "", 0

    def _start_engine(self) -> None:
        """Bagian yang dipakai kedua mode (web & native)."""

        # 1) pembaca ZeroMQ (selalu jalan, tidak peduli decoder asli/hilang)
        self.decoder = DecoderClient(self.engine, self.cfg.zmq_endpoint)
        self.decoder.start()

        # 2) pemeriksaan perangkat + supervisor decoder (Windows/desktop)
        threading.Thread(target=self._check_device, name="device-check",
                         daemon=True).start()
        self.supervisor = DecoderManager(self.cfg, self.engine,
                                         on_change=self._on_decoder_status)
        self.ctx.decoder_manager = self.supervisor
        self.supervisor.device_changed(self._device_seen)
        if self.cfg.manage_decoder:
            self.supervisor.start()
        else:
            self.engine.log("info", "Decoder dikelola sendiri oleh pengguna "
                                    "(YurLaps hanya mendengarkan ZeroMQ)")

        # 3) pembaruan aplikasi
        self.updater = Updater(self.cfg, self.engine)
        self.ctx.updater = self.updater
        if self.cfg.auto_check_updates and self.updater.manifest_url():
            self.updater.check_async()

        self.ctx.cloud.start()

        # Optional legacy live-snapshot feed is independent of CloudSync.
        self.online = OnlineSync(self.engine)
        if self.cfg.online_enabled and (self.cfg.online_url or "").strip():
            self.online.start()
        else:
            self.engine.log("info", "Feed snapshot tambahan tidak dikonfigurasi")

        threading.Thread(target=self._tick_loop, name="tick", daemon=True).start()
        threading.Thread(target=self._device_loop, name="device-poll",
                         daemon=True).start()
    _device_seen = None

    def _device_loop(self, every: float = 5.0) -> None:
        """Pantau YurLaps Decoder berkala → tersambung/terputus langsung terdeteksi."""
        while not self._stop.is_set():
            try:
                self._check_device()
            except Exception as exc:  # pragma: no cover
                self.engine.log("warn", f"Cek perangkat: {exc}")
            self._stop.wait(every)

    def _hint_lan(self, port: int) -> None:
        """Beri tahu alamat yang bisa dibuka dari HP + catatan firewall."""
        try:
            from .net import lan_ips
            ips = lan_ips()
        except Exception:
            ips = []
        if not ips:
            self.engine.log("info", "Tidak ada alamat LAN terdeteksi — HP/TV belum "
                                    "bisa membuka. Pastikan komputer tersambung ke "
                                    "Wi-Fi/router yang sama.")
            return
        for ip in ips:
            self.engine.log("info", f"Buka dari HP: http://{ip}:{port}"
                                    + (f"/?t={self.cfg.access_token[:6]}…"
                                       if self.cfg.access_token else ""))
        if os.name == "nt":
            self.engine.log("info", "Kalau HP tidak bisa membuka alamat di atas, "
                                    "izinkan YurLaps di Windows Firewall: pengaturan "
                                    "ini butuh hak administrator sekali saja (lihat "
                                    "README bagian 'Buka dari HP').")

    def _check_device(self) -> None:
        """Periksa YurLaps Decoder. Dipanggil saat start dan berkala: perangkat
        yang tersambung/terputus saat aplikasi berjalan langsung terdeteksi."""
        try:
            from .devices import detect, can_install_driver
            info = detect(self.cfg.driver_vid, self.cfg.driver_pid)
            self.engine.device_info = info.to_dict()
            ok, why = can_install_driver()
            self.engine.device_info["can_install"] = ok
            self.engine.device_info["install_reason"] = why
            prev = self._device_seen
            now_seen = bool(getattr(info, "present", False))
            self._device_seen = now_seen
            supervisor = self.supervisor
            if supervisor is not None:
                supervisor.device_changed(now_seen)
            # hanya catat di log kalau statusnya berubah, jangan banjiri log
            if prev != now_seen or prev is None:
                if now_seen:
                    self.engine.log("ok", f"YurLaps Decoder terdeteksi: {info.message}")
                else:
                    self.engine.log("warn", f"YurLaps Decoder tidak terdeteksi: {info.message}")
                    self.engine.log("info", "Decoder akan tersambung sendiri "
                                            "begitu YurLaps Decoder disambungkan.")
            self.engine.version += 1
        except Exception as exc:
            self.engine.log("warn", f"Pemeriksaan YurLaps Decoder gagal: {exc}")

    def _on_decoder_status(self, status: dict) -> None:
        self.engine.decoder_status_extra = status
        self.engine.version += 1
        self.bus.publish({"kind": "decoder", "at": self.engine.now(), **status})

    def _tick_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.engine.tick()
                self.ctx.tick()
                if self.ctx._race is not None:
                    self.ctx.race.cek_waktu()
            except Exception as exc:  # pragma: no cover
                self.engine.log("error", f"tick: {exc}")
            self._stop.wait(1.0)

    def stop(self) -> None:
        self._stop.set()
        if self.decoder is not None:
            self.decoder.stop()
            self.decoder.join(timeout=5)
        try:
            self.ctx.practice.close_all("application_closed")
        except Exception as exc:
            self.engine.log("error", f"Practice gagal disimpan saat aplikasi ditutup: {exc}")
        if self.ctx._cloud is not None:
            self.ctx.cloud.stop()
        for th in (self.online, self.supervisor):
            try:
                if th:
                    th.stop()
            except Exception:
                pass
        # tutup stint yang masih terbuka
        try:
            for d in list(self.engine.drivers.values()):
                if d.current:
                    self.engine.close_stint(d.transponder_id, reason="aplikasi ditutup")
        except Exception:
            pass
        try:
            if self.httpd:
                self.httpd.shutdown()
        except Exception:
            pass
        try:
            self.storage.close()
        except Exception:
            pass


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="yurlaps", description="YurLaps — lap timing untuk YurLaps Decoder")
    p.add_argument("--host", default=None, help="alamat bind (default 0.0.0.0)")
    p.add_argument("--port", type=int, default=None, help="port HTTP (default 8080)")
    p.add_argument("--endpoint", default=None, help="endpoint ZMQ decoder (default tcp://127.0.0.1:5556)")
    p.add_argument("--data-dir", default=None, help="folder data (portable mode)")
    p.add_argument("--no-browser", action="store_true", help="jangan buka browser")
    p.add_argument("--native", action="store_true",
                   help="alias kompatibilitas untuk --app-window (UI lengkap)")
    p.add_argument("--electron", action="store_true",
                   help="dijalankan oleh jendela aplikasi Electron "
                        "(server lokal 127.0.0.1, port acak)")
    p.add_argument("--web", action="store_true",
                   help="mode web: buka port HTTP untuk HP/TV")
    p.add_argument("--desktop", action="store_true", help="buka di jendela aplikasi (pywebview)")
    p.add_argument("--browser", action="store_true",
                   help="paksa mode browser biasa (tanpa jendela aplikasi)")
    p.add_argument("--app-window", action="store_true",
                   help="buka UI dalam jendela aplikasi (Edge/Chrome mode --app)")
    p.add_argument("--open-folder", action="store_true",
                   help="buka folder data (untuk shortcut)")
    return p


def run_electron(args) -> int:
    """Dijalankan oleh shell Electron (Node) sebagai proses anak.

    Protokolnya sederhana: cetak satu baris JSON berisi port ke stdout, lalu
    tunggu sampai induk menutup stdin atau mengirim SIGTERM. Tidak ada jendela,
    tidak ada browser, tidak ada port yang terbuka ke jaringan.
    """
    import json
    import signal
    import threading

    _safe_console()
    app = YurLaps(args)
    host, port = app.start_electron()
    print(json.dumps({"event": "ready", "host": host, "port": port,
                      "version": APP_VERSION}), flush=True)

    stop = threading.Event()

    def _on_term(*_a):
        stop.set()

    try:
        signal.signal(signal.SIGTERM, _on_term)
    except Exception:
        pass

    def _watch_stdin():
        # Induk menutup stdin saat jendelanya ditutup -> aplikasi berhenti.
        try:
            while sys.stdin.readline():
                pass
        except Exception:
            pass
        stop.set()

    threading.Thread(target=_watch_stdin, name="stdin-watch", daemon=True).start()
    try:
        while not stop.wait(0.4):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        try:
            app.stop()
        except Exception:
            pass
    return 0


def main(argv=None) -> int:
    _safe_console()
    args = build_parser().parse_args(argv)

    if getattr(args, "open_folder", False):
        target = default_data_dir()
        target.mkdir(parents=True, exist_ok=True)
        try:
            if os.name == "nt":
                os.startfile(str(target))          # type: ignore[attr-defined]
            else:
                webbrowser.open(target.as_uri())
        except Exception:
            print(target)
        return 0

    # All launcher modes use the same complete UI.
    # Shell Electron memanggil: python -m app --electron
    if getattr(args, "electron", False):
        return run_electron(args)

    if getattr(args, "native", False):
        args.app_window = True

    if args.desktop or args.app_window:
        from .desktop import run_desktop
        return run_desktop(args)

    print(BANNER.format(ver=APP_VERSION))
    app = YurLaps(args)
    host, port = app.start()

    url = f"http://localhost:{port}"
    if app.cfg.access_token:
        url += f"/?t={app.cfg.access_token}"
    print(f"\n  UI     : {url}")
    for ip in lan_ips():
        print(f"  LAN    : http://{ip}:{port}")
    print(f"  Decoder: {app.cfg.zmq_endpoint}")
    print(f"  Data   : {default_data_dir()}")
    print("\n  Ctrl+C untuk keluar.\n")

    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    def _sig(*_):
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGINT, _sig)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _sig)
    except Exception:
        pass

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n  Menutup YurLaps ...")
    finally:
        app.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
