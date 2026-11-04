"""Uji integrasi: HTTP API + SSE + sinkronisasi online.

Jalankan:  python tests/test_integration.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:                     # konsol Windows (cp1252) tidak bisa cetak karakter khusus
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass
os.environ["YURLAPS_DATA"] = tempfile.mkdtemp(prefix="stintlab-int-")

from app.config import Config                            # noqa: E402
from tests.sim_decoder import SimulatedDecoder          # noqa: E402
from app.engine import Engine                            # noqa: E402
from app.online import OnlineSync                        # noqa: E402
from app.storage import Storage                          # noqa: E402
from app.webapp import AppContext, EventBus, start_server  # noqa: E402


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        raise SystemExit(1)


def get_json(url, token=None):
    req = urllib.request.Request(url)
    if token:
        req.add_header("X-YurLaps-Token", token)
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def post_json(url, body, token=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-YurLaps-Token", token)
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def main():
    print("YurLaps integration test\n")
    cfg = Config.load()
    cfg.mappings = {}
    cfg.host = "127.0.0.1"
    cfg.port = 0
    cfg.access_token = "rahasia"
    cfg.idle_split_seconds = 120
    cfg.min_lap_seconds = 0.2

    store = Storage()
    bus = EventBus()
    engine = Engine(cfg, storage=store, on_event=bus.publish)
    ctx = AppContext(cfg, engine, store, bus)
    httpd = start_server(ctx, "127.0.0.1", 0, block=False)
    port = httpd.server_address[1]
    base = f"http://127.0.0.1:{port}"

    # --- endpoint publik -------------------------------------------------
    h = get_json(base + "/api/health")
    check(h["ok"] and h["app"] == "YurLaps", "/api/health publik")

    # --- proteksi token --------------------------------------------------
    try:
        get_json(base + "/api/state")
        check(False, "/api/state tanpa token harus 401")
    except urllib.error.HTTPError as e:
        check(e.code == 401, "/api/state tanpa token ditolak (401)")
    st = get_json(base + "/api/state", token="rahasia")
    check(st["ok"] and "drivers" in st, "/api/state dengan token OK")

    # --- UI statis -------------------------------------------------------
    with urllib.request.urlopen(base + "/", timeout=5) as r:
        html = r.read().decode()
    check("YurLaps" in html and "EventSource" in html, "UI tersaji di /")

    # --- simulasi lap nyata lewat ZMQ + SSE ------------------------------
    cfg.port = port
    sim = SimulatedDecoder("tcp://127.0.0.1:5591", transponders=(900,), lap_seconds=(0.5,))
    sim.start()

    from app.decoder import DecoderClient
    dec = DecoderClient(engine, "tcp://127.0.0.1:5591")
    time.sleep(0.5)
    dec.start()

    events = []
    def sse_reader():
        req = urllib.request.Request(base + f"/api/events?t=rahasia")
        try:
            with urllib.request.urlopen(req, timeout=6) as r:
                for raw in r:
                    line = raw.decode().strip()
                    if line.startswith("data: "):
                        events.append(json.loads(line[6:]))
        except Exception:
            pass

    t = threading.Thread(target=sse_reader, daemon=True)
    t.start()
    time.sleep(5.0)
    dec.stop()
    sim.stop()
    kinds = {e.get("kind") for e in events}
    check("hello" in kinds, "SSE mengirim state awal")
    check(any(e.get("kind") == "lap" for e in events), "event lap mengalir via SSE")

    st = get_json(base + "/api/state", token="rahasia")
    check(len(st["drivers"]) >= 1, "state berisi pembalap")
    d = st["drivers"][0]
    check(d["total_laps"] >= 1, f"lap tercatat di state ({d['total_laps']})")

    # --- ubah data pembalap ---------------------------------------------
    r = post_json(base + "/api/driver", {"transponder_id": 900, "driver": "Rudi",
                                        "car": "Schumacher"}, token="rahasia")
    check(r["ok"] and r["state"]["drivers"][0]["driver"] == "Rudi", "PATCH data pembalap")

    # --- setelan ---------------------------------------------------------
    r = post_json(base + "/api/settings", {"idle_split_seconds": 45,
                                           "track_name": "Sirkuit Medan RC"}, token="rahasia")
    check(r["ok"] and r["state"]["settings"]["idle_split_seconds"] == 45, "simpan setelan")
    check(Config.load().track_name == "Sirkuit Medan RC", "setelan persist ke config.json")

    # --- CSV -------------------------------------------------------------
    with urllib.request.urlopen(base + f"/api/export.csv?t=rahasia", timeout=5) as resp:
        csv_body = resp.read().decode("utf-8-sig")
    check(csv_body.splitlines()[0].startswith("waktu,transponder_id"), "unduh CSV")
    check(len(csv_body.strip().splitlines()) >= 2, "CSV berisi lap")

    # --- sinkronisasi online ke server contoh ---------------------------
    try:
        import flask  # noqa: F401
        received = {}

        def fake_ingest():
            from flask import Flask, request, jsonify
            app = Flask("ingest")
            @app.post("/api/stintlab/ingest")
            def ingest():
                received.update(request.get_json())
                return jsonify(ok=True)
            app.run(host="127.0.0.1", port=panel_port, debug=False, use_reloader=False)

        import socket
        s = socket.socket(); s.bind(("127.0.0.1", 0)); panel_port = s.getsockname()[1]; s.close()
        threading.Thread(target=fake_ingest, daemon=True).start()
        time.sleep(1.2)

        cfg.online_enabled = True
        cfg.online_url = f"http://127.0.0.1:{panel_port}"
        cfg.online_token = "tok"
        sync = OnlineSync(engine)
        ok = sync.push_once()
        check(ok, f"POST ke website hasil berhasil ({sync.last_error or 'no error'})")
        check(received.get("drivers") and received["drivers"][0]["driver"] == "Rudi",
              "payload online berisi data pembalap")
    except ImportError:
        print("  skip  (flask tidak terpasang, uji online dilewati)")

    httpd.shutdown()
    store.close()
    print("\nSemua tes integrasi lulus [OK]")


if __name__ == "__main__":
    main()
