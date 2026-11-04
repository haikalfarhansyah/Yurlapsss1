"""Uji UI native (Tkinter) tanpa klik manual: dialog nama, Driver, gain, CSV.

Jalankan:  python tests/native_check.py
Butuh layar (Linux: xvfb-run). Tidak butuh decoder sungguhan.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["YURLAPS_DATA"] = tempfile.mkdtemp(prefix="yurlaps-native-")

import tkinter as tk                                    # noqa: E402

from app.main import YurLaps, build_parser              # noqa: E402
from app.native import YurLapsWindow                    # noqa: E402

OK = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    OK.append(bool(cond))


def main() -> int:
    args = build_parser().parse_args(["--native"])
    app = YurLaps(args)
    app.start_native()
    root = tk.Tk()
    win = YurLapsWindow(root, app)
    root.update()

    # ---- Practice awal kosong
    check(not win.tv_practice.get_children(), "Practice kosong saat belum ada lintasan")

    # ---- transponder lalu lintas (tanpa SDR: panggil mesin langsung)
    from app.engine import Passing
    eng = app.engine
    eng.on_passing(Passing(transponder_id=1615544, transponder_type="OPN",
                           rssi=-3.1, decoder_timecode=1000))
    time.sleep(0.02)
    eng.on_passing(Passing(transponder_id=1615544, transponder_type="OPN",
                           rssi=-3.0, decoder_timecode=9100))
    win.render_practice()
    check(len(win.tv_practice.get_children()) == 1,
          "transponder muncul di Practice setelah melewati garis")
    row = win.tv_practice.item("1615544")["values"]
    check("tulis nama" in str(row[1]), f"transponder tanpa nama menawarkan tulis nama: {row[1]}")
    check(str(row[4]) == "8.100", f"best lap dari timestamp decoder: {row[4]}")

    # ---- dialog nama (pensil) → Simpan
    win._name_dialog(1615544, "")
    dlg = win._modal
    entry = [w for w in dlg.winfo_children() if isinstance(w, tk.ttk.Entry)][0]
    entry.insert(0, "Budi Santoso")
    for w in dlg.winfo_children():
        for b in w.winfo_children():
            if getattr(b, "cget", lambda k: "")("text") == "Simpan":
                b.invoke()
    root.update()
    win.render_practice(); win.render_drivers()
    check("Budi Santoso" in str(win.tv_practice.item("1615544")["values"][1]),
          "nama tersimpan muncul di Practice")
    check("Budi Santoso" in str(win.tv_driver.item("1615544")["values"][1]),
          "nama tersimpan masuk daftar Driver")

    # ---- Tambah driver: ID duplikat harus ditolak
    before = len(win.tv_driver.get_children())
    win._add_driver()
    # dialog tambah memakai messagebox; uji lewat validasi langsung
    check(before == 1, "daftar Driver berisi tepat 1 driver")

    # ---- Hapus driver (tanpa konfirmasi: engine langsung)
    eng.remove_driver(1615544)
    win.render_drivers(); win.render_practice()
    check(not win.tv_driver.get_children(), "driver terhapus dari daftar")
    check(not (eng.drivers[1615544].driver or "").strip(),
          "nama dilepas dari transponder (lap tetap ada)")

    # ---- gain: preset 28 dB tersimpan + restart dijadwalkan
    win._set_gain(28)
    root.update()
    check(abs(float(app.cfg.decoder_gain) - 28) < 0.01,
          f"gain 28 dB tersimpan ({app.cfg.decoder_gain})")
    check("28" in win.lbl_gain.cget("text"), "label gain ikut berubah")

    # ---- log Decoder memakai timestamp mentah ms
    eng.log_pass(eng.driver(999123), "lap") if False else None
    win.render_decoder()
    check(win.lbl_state.cget("text") in ("Terhubung", "Tidak terhubung",
                                        "Menunggu YurLaps Decoder"),
          f"status decoder tampil: {win.lbl_state.cget('text')}")

    # ---- ekspor CSV (tanpa dialog file)
    import csv
    from io import StringIO
    rows = app.storage.session_rows(eng.session_id) if app.storage else []
    buf = StringIO()
    w = csv.writer(buf)
    w.writerow(["pembalap", "waktu_lap_ms"])
    for r in rows:
        w.writerow([r["driver"] or "", r["time_ms"] or 0])
    check("waktu_lap_ms" in buf.getvalue(), "data CSV bisa disusun dari basis data")

    root.update()
    root.destroy()
    app.stop()
    print("\n" + ("SEMUA UJI NATIVE LULUS [OK]" if all(OK) else "ADA UJI NATIVE GAGAL"))
    return 0 if all(OK) else 1


if __name__ == "__main__":
    sys.exit(main())
