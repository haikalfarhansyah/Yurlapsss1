"""Tes supervisor decoder + pembaruan aplikasi.

Jalankan:  python tests/test_manager.py

Tidak butuh hardware: "decoder" dibuat sebagai skrip kecil yang mem-publish
pesan OpenStint sintetis lalu keluar sendiri — persis seperti decoder yang
crash, sehingga kita bisa menguji restart otomatis.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:                     # konsol Windows (cp1252) tidak bisa cetak karakter khusus
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

DATA = tempfile.mkdtemp(prefix="stintlab-mgr-")
os.environ["YURLAPS_DATA"] = DATA    # folder data YurLaps

from app.config import Config, APP_VERSION                  # noqa: E402
from app.decoder import DecoderClient                       # noqa: E402
from app.decoder_manager import DecoderManager              # noqa: E402
from app.engine import Engine                               # noqa: E402
from app.storage import Storage                             # noqa: E402
from app.updater import Updater, is_newer, parse_version    # noqa: E402


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# "decoder" palsu: skrip python yang publish 3 lap lalu keluar (simulasi crash)
# ---------------------------------------------------------------------------
FAKE_DECODER = '''#!/usr/bin/env python3
import sys, time, random
sys.path.insert(0, {root!r})
from tests.sim_decoder import SimulatedDecoder
sim = SimulatedDecoder("tcp://127.0.0.1:5593", transponders=(555,), lap_seconds=(0.3,))
sim.start()
time.sleep(4.5)          # hidup 3 detik lalu keluar sendiri (crash)
sim.stop()
sys.exit(3)
'''


def write_fake_decoder(root: Path) -> Path:
    path = root / "bin" / "openstint_rtlsdr"   # sengaja tanpa .exe (skrip)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(FAKE_DECODER.format(root=str(Path(__file__).resolve().parent.parent)))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


# ---------------------------------------------------------------------------
def test_version_compare():
    check(is_newer("0.2.0", "0.1.0"), "0.2.0 > 0.1.0")
    check(not is_newer("0.1.0", "0.1.0"), "versi sama bukan pembaruan")
    check(is_newer("1.10.0", "1.9.9"), "perbandingan numerik (bukan string)")
    check(parse_version("v0.12.3") == (0, 12, 3), "parse versi dengan prefix v")


def test_manager_autorestart():
    print("\nSupervisor decoder")
    cfg = Config.load()
    cfg.manage_decoder = True
    cfg.decoder_args = ""
    cfg.decoder_idle_restart = 60          # jangan restart karena idle di tes ini
    cfg.decoder_restart_max_delay = 2
    cfg.decoder_auto_download = False
    cfg.min_lap_seconds = 0.2        # lap sintetis cepat (0.3 s) harus diterima

    exe = write_fake_decoder(Path(DATA))
    cfg.decoder_exe = str(exe)
    cfg.decoder_args = ""

    store = Storage()
    eng = Engine(cfg, storage=store)
    dec = DecoderClient(eng, "tcp://127.0.0.1:5593")
    dec.start()

    statuses = []
    mgr = DecoderManager(cfg, eng, on_change=lambda s: statuses.append(s))
    mgr.start()
    time.sleep(7.5)                        # cukup untuk 1x hidup + 1x restart

    d = list(eng.drivers.values())
    check(len(d) >= 1, f"transponder dari decoder terdeteksi ({len(d)})")
    check(d and d[0].total_laps >= 1, f"lap tercatat lewat proses decoder "
                                      f"({d[0].total_laps if d else 0})")
    check(mgr.status.restarts >= 1, f"restart otomatis terjadi "
                                    f"({mgr.status.restarts}×)")
    check(any(s.get("state") == "running" for s in statuses),
          "status 'running' dilaporkan ke UI")
    check(any("berhenti" in (s.get("message") or "") for s in statuses),
          "pesan berhenti dilaporkan ke UI")

    # restart manual dari UI
    before = mgr.status.restarts
    mgr.restart_now()
    time.sleep(3.5)
    check(mgr.status.running, "restart manual menghidupkan decoder lagi")

    mgr.stop()
    dec.stop()
    time.sleep(0.5)
    check(mgr.status.running is False, "stop() mematikan proses decoder")
    store.close()


def test_manager_idle_detect():
    print("\nDeteksi decoder macet (tidak ada data)")
    cfg = Config.load()
    cfg.manage_decoder = True
    cfg.decoder_args = ""
    cfg.decoder_idle_restart = 3
    cfg.decoder_auto_download = False

    # skrip yang hidup tapi tidak pernah mengirim data -> harus di-restart
    path = Path(DATA) / "bin" / "openstint_silent"
    path.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(60)\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    cfg.decoder_exe = str(path)

    eng = Engine(cfg)
    mgr = DecoderManager(cfg, eng)
    mgr.start()
    time.sleep(9.0)
    check(mgr.status.restarts >= 1,
          f"decoder tanpa data di-restart ({mgr.status.restarts}×)")
    mgr.stop()


def test_updater():
    print("\nPembaruan aplikasi")
    work = Path(DATA) / "upd"
    (work / "payload").mkdir(parents=True, exist_ok=True)
    (work / "payload" / "YurLaps.exe").write_bytes(b"versi-baru")
    (work / "payload" / "app").mkdir(exist_ok=True)
    (work / "payload" / "app" / "baru.txt").write_text("isi baru")
    zip_path = work / "update.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for f in (work / "payload").rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(work / "payload"))
    import hashlib
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()

    # server manifest + file zip
    manifest = {"version": "9.9.9", "notes": "tes", "url": "", "sha256": digest,
                "size": zip_path.stat().st_size}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.startswith("/update.zip"):
                body = zip_path.read_bytes()
                ctype = "application/zip"
            else:
                body = json.dumps(manifest).encode()
                ctype = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    manifest["url"] = f"http://127.0.0.1:{port}/update.zip"

    cfg = Config.load()
    cfg.update_manifest_url = f"http://127.0.0.1:{port}/update.json"
    cfg.save()
    upd = Updater(cfg)
    info = upd.check()
    check(info["state"] == "available" and info["latest"] == "9.9.9",
          "manifest dibaca & versi baru terdeteksi")

    upd._download()
    check(upd.state == "ready", f"unduhan selesai (state={upd.state} "
                                f"err={upd.error})")
    staging = Path(DATA) / "updates" / "staging"
    check((staging / "YurLaps.exe").read_bytes() == b"versi-baru",
          "isi paket diekstrak ke staging")
    check((staging / "app" / "baru.txt").is_file(), "struktur folder dipertahankan")

    # checksum salah harus ditolak
    manifest["sha256"] = "0" * 64
    upd2 = Updater(cfg)
    upd2.check()
    upd2._download()
    check(upd2.state == "error" and "Checksum" in upd2.error,
          "checksum salah ditolak")

    httpd.shutdown()


def test_manifest_helper():
    from app.updater import build_manifest
    p = Path(DATA) / "x.zip"
    p.write_bytes(b"abc")
    m = build_manifest("0.2.0", "https://contoh.id/a.zip", "catatan", str(p))
    check(m["version"] == "0.2.0" and m["size"] == 3 and len(m["sha256"]) == 64,
          "tools pembuat manifest menghasilkan sha256 + size")


if __name__ == "__main__":
    print("YurLaps - tes perangkat, supervisor decoder, dan pembaruan\n")
    test_version_compare()
    test_manager_autorestart()
    test_manager_idle_detect()
    test_updater()
    test_manifest_helper()
    print("\nSemua tes manajemen perangkat lulus [OK]  (versi saat ini "
          f"{APP_VERSION})")
