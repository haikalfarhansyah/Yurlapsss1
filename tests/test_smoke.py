"""Tes cepat YurLaps: engine, storage, dan integrasi ZMQ simulator.

Jalankan:  python tests/test_smoke.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:                     # konsol Windows (cp1252) tidak bisa cetak karakter khusus
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

TMP = tempfile.mkdtemp(prefix="stintlab-test-")
os.environ["YURLAPS_DATA"] = TMP

from app.config import Config                     # noqa: E402
from app.decoder import parse_passing, DecoderClient  # noqa: E402
from tests.sim_decoder import SimulatedDecoder  # noqa: E402
from app.engine import Engine, Passing, fmt_ms_short  # noqa: E402
from app.storage import Storage                   # noqa: E402


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        raise SystemExit(1)


def test_gain_args():
    cfg = Config()
    cfg.decoder_gain = 32
    cfg.decoder_bias_tee = True
    cfg.decoder_args = ""
    args = cfg.effective_decoder_args()
    check(args[:2] == ["-g", "32"], "gain disusun dari setelan: " + " ".join(args))
    check("-b" in args, "bias tee ikut sebagai -b")
    old = Config()
    old.decoder_args = "-g 40 -b -m"
    old.decoder_gain = 20
    # migrasi setelan lama (gain masih di string argumen)
    import json, pathlib, tempfile
    d = pathlib.Path(tempfile.mkdtemp(prefix="stintlab-cfg-"))
    (d / "config.json").write_text(json.dumps({"decoder_args": "-g 40 -b -m"}))
    import os
    os.environ["YURLAPS_DATA"] = str(d)          # nama folder data baru
    os.environ.pop("STINTLAB_DATA", None)
    from app import config as cfgmod
    c2 = cfgmod.Config.load()
    check(c2.decoder_gain == 40 and c2.decoder_bias_tee and c2.decoder_monitor,
          "setelan lama '-g 40 -b -m' dimigrasikan ke setelan terstruktur")
    os.environ["YURLAPS_DATA"] = TMP


def test_pass_log():
    from app.engine import Engine
    eng = Engine(Config())
    p = parse_passing("P 1234 OPN 7654321 -2.10 55 91000 12.5")
    d = eng.driver(p.transponder_id, p.transponder_type)
    eng.log_pass(p, d, "lap", lap_ms=9123, seq=1)
    entry = eng.pass_log[-1]
    check(entry["transponder_id"] == 7654321 and entry["rssi"] == -2.10
          and entry["mer"] == 12.5 and entry["hit_count"] == 55
          and entry["pass_duration_us"] == 91000
          and entry["decoder_timecode"] == 1234,
          "log lintasan menyimpan id/rssi/mer/hit/durasi/timecode")
    eng.decoder_status_message("S 1500 -41.5 5.08 200 180 2.5".split())
    check(eng.decoder_status["noise_power"] == -41.5, "noise dari pesan S tersimpan")
    check(len(eng.noise_history) == 1, "riwayat noise untuk grafik terisi")


def test_lap_from_decoder_timestamp():
    """Lap time wajib diambil dari timestamp decoder (ms), bukan jam komputer."""
    from app.engine import Engine, Passing
    cfg = Config()
    cfg.min_lap_seconds = 1.0
    eng = Engine(cfg)
    tid = 5550001
    # lintasan 1 -> titik nol
    eng.on_passing(Passing(transponder_id=tid, transponder_type="OPN",
                           rssi=-3.0, decoder_timecode=100000))
    # lintasan 2 -> harus 9123 ms (selisih timestamp), walau jam komputer ~0
    lap = eng.on_passing(Passing(transponder_id=tid, transponder_type="OPN",
                                 rssi=-3.2, decoder_timecode=109123))
    check(lap is not None and lap.time_ms == 9123,
          f"lap dihitung dari timestamp decoder: {lap.time_ms if lap else None} ms")
    check(lap.decoder_timecode == 109123, "timestamp lintasan disimpan di lap")

    # decoder restart -> timestamp mundur, harus jatuh ke jam komputer (tidak negatif)
    lap2 = eng.on_passing(Passing(transponder_id=tid, transponder_type="OPN",
                                  rssi=-3.1, decoder_timecode=120))
    check(lap2 is None, "timestamp mundur tidak menghasilkan lap negatif")

    # tanpa timestamp (decoder lain) -> tetap jalan pakai jam komputer
    eng2 = Engine(Config())
    eng2.cfg.min_lap_seconds = 0.0
    t2 = 5550002
    eng2.on_passing(Passing(transponder_id=t2))
    time.sleep(0.05)
    lap3 = eng2.on_passing(Passing(transponder_id=t2))
    check(lap3 is not None and lap3.time_ms >= 40,
          f"tanpa timestamp tetap dihitung dari jam komputer: {lap3.time_ms if lap3 else None} ms")


def test_parse():
    p = parse_passing("P 1618706341 OPN 1615544 3.50 64 89113 24.3")
    check(p is not None and p.transponder_id == 1615544, "parse pesan P")
    check(p.rssi == 3.50 and p.pass_duration_us == 89113 and p.mer == 24.3,
          "parse atribut P lengkap")
    check(parse_passing("S 1 -41 5 0 0 3") is None, "pesan S bukan passing")
    check(parse_passing("P 1 OPN") is None, "pesan cacat tidak crash")


def test_engine_logic():
    cfg = Config.load()
    cfg.mappings = {}
    cfg.idle_split_seconds = 3
    cfg.min_lap_seconds = 1.0
    store = Storage()
    eng = Engine(cfg, storage=store)

    # lintasan pertama -> buka stint, bukan lap
    check(eng.on_passing(Passing(111, "OPN")) is None, "lintasan pertama buka stint")
    eng.drivers[111].last_pass_mono -= 2.0   # pura-pura 2 detik lalu
    lap = eng.on_passing(Passing(111, "OPN", rssi=-3.1, mer=11.2))
    check(lap is not None and 1900 <= lap.time_ms <= 2100, f"lap pertama ~2000ms ({lap.time_ms})")

    # lintasan terlalu cepat -> diabaikan
    ghost = eng.on_passing(Passing(111, "OPN"))
    check(ghost is None, "lap < min_lap_seconds diabaikan (anti-ghost)")

    # lap valid berikutnya
    eng.drivers[111].last_pass_mono -= 3.0
    lap2 = eng.on_passing(Passing(111, "OPN"))
    check(lap2 is not None and lap2.seq == 2, "lap kedua tercatat (seq=2)")

    d = eng.drivers[111]
    check(d.total_laps == 2, "total lap = 2")
    check(d.best_lap is not None, "best lap terhitung")
    check(d.current is not None and d.current.index == 1, "stint masih terbuka")

    # data pembalap tersimpan ke config
    eng.update_driver(111, driver="Budi", car="Xray X4")
    check(Config.load().mappings["111"].driver == "Budi", "nama pembalap persist di config")
    check(any(r["driver"] == "Budi" for r in store.drivers()), "nama pembalap persist di DB")

    # idle -> stint ditutup, lap berikut membuka stint baru
    eng.drivers[111].last_pass_mono -= 10
    eng.tick()
    check(d.current is None, "stint ditutup setelah idle")
    eng.on_passing(Passing(111, "OPN"))
    check(d.current is not None and d.current.index == 2, "stint #2 dibuka otomatis")

    # snapshot & payload online
    snap = eng.snapshot()
    check(snap["drivers"] and snap["drivers"][0]["best_ms"], "snapshot berisi best lap")
    payload = eng.online_payload()
    check(payload["drivers"][0]["driver"] == "Budi", "payload online benar")

    # ekspor CSV
    csv_path = os.path.join(TMP, "out.csv")
    store.export_session_csv(eng.session_id, csv_path)
    body = open(csv_path, encoding="utf-8-sig").read().strip().splitlines()
    check(len(body) == 3, f"CSV berisi header + 2 lap (baris={len(body)})")
    store.close()


def test_zmq_end_to_end():
    """Uji pipa ZeroMQ: simulator -> DecoderClient -> engine.

    Dibuat tahan-waktu. Catatan penting: ZeroMQ PUB/SUB bersifat "slow joiner"
    - pesan yang dikirim sebelum langganan terbentuk akan HILANG. Karena itu
    subscriber didahulukan, pesan diulang terus oleh simulator, dan hasilnya
    diperiksa dengan beberapa kriteria supaya tidak gagal acak di CI.
    """
    os.environ["YURLAPS_DATA"] = tempfile.mkdtemp(prefix="stintlab-zmq-")
    cfg = Config.load()
    cfg.mappings = {}
    cfg.idle_split_seconds = 120
    cfg.min_lap_seconds = 0.1
    eng = Engine(cfg)
    endpoint = "tcp://127.0.0.1:5599"

    dec = DecoderClient(eng, endpoint)
    dec.start()
    time.sleep(0.5)                       # beri waktu connect

    sim = SimulatedDecoder(endpoint, transponders=(777,), lap_seconds=(0.3,))
    sim.start()

    deadline = time.time() + 20
    driver = None
    while time.time() < deadline:
        time.sleep(0.4)
        if eng.drivers:
            driver = list(eng.drivers.values())[0]
            if driver.passes >= 3:
                break

    dec.stop()
    sim.stop()
    time.sleep(0.2)

    # --- diagnosis yang berguna kalau gagal ------------------------------
    detail = (f"counter={dec.rx_counts}, driver="
              f"{driver.passes if driver else 'tidak ada'}, "
              f"decoder_online={eng.decoder_online}")
    check(dec.rx_counts["P"] >= 1, f"pesan P diterima lewat ZMQ ({detail})")
    check(driver is not None, f"transponder simulasi terdeteksi ({detail})")
    check(driver is not None and driver.passes >= 2, f"lintasan tercatat ({detail})")

    if driver:
        print(f"       contoh: {driver.name} - {driver.total_laps} lap - "
              f"best {fmt_ms_short(driver.best_lap.time_ms if driver.best_lap else None)}")


if __name__ == "__main__":
    print("YurLaps smoke test\n")
    test_parse()
    test_gain_args()
    test_lap_from_decoder_timestamp()
    test_pass_log()
    test_engine_logic()
    test_zmq_end_to_end()
    print("\nSemua tes lulus [OK]")
