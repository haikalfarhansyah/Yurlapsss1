"""Contoh server penerima data online YurLaps (Flask).

Ini contoh minimal: menyimpan snapshot terakhir dari setiap instance dan
menyajikan halaman hasil live sederhana. Jalankan di VPS:

    pip install flask
    python ingest_server_example.py            # port 9000

Lalu di YurLaps → Setelan → "Sinkron ke website hasil":
    URL       : http://IP-VPS:9000
    Token     : (samakan dengan INGEST_TOKEN di bawah, opsional)

Halaman hasil:  http://IP-VPS:9000/
Snapshot JSON:  http://IP-VPS:9000/api/results

Catatan keamanan: letakkan di belakang HTTPS (Caddy/Nginx) kalau dipakai
publik, dan selalu isi token.
"""
from __future__ import annotations

import os
import time

from flask import Flask, jsonify, request, abort, Response

INGEST_TOKEN = os.environ.get("INGEST_TOKEN", "")
app = Flask(__name__)
STATE: dict[str, dict] = {}


def _check_auth() -> None:
    if not INGEST_TOKEN:
        return
    auth = request.headers.get("Authorization", "")
    if auth.replace("Bearer ", "").strip() != INGEST_TOKEN:
        abort(401)


@app.post("/api/stintlab/ingest")
def ingest():
    _check_auth()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or "drivers" not in payload:
        abort(400, "payload tidak valid")
    key = payload.get("track") or payload.get("instance") or request.remote_addr
    STATE[key] = payload
    return jsonify({"ok": True, "received_at": time.time()})


@app.get("/api/results")
def results():
    return jsonify(STATE)


@app.get("/")
def home():
    parts = []
    for track, snap in STATE.items():
        sess = snap.get("session", {})
        rows = []
        for i, d in enumerate(snap.get("drivers", []), 1):
            best = d.get("best_ms")
            last = d.get("last_ms")
            rows.append(
                f"<tr><td>{i}</td><td>{_e(d.get('driver') or d.get('transponder_id'))}</td>"
                f"<td>{_e(d.get('car') or '')}</td><td>{d.get('total_laps') or 0}</td>"
                f"<td class=best>{_fmt(best)}</td><td>{_fmt(last)}</td>"
                f"<td>{'● live' if d.get('stint_open') else ''}</td></tr>")
        parts.append(f"""
        <section>
          <h2>{_e(track)}</h2>
          <p class=sub>{_e(snap.get('event',''))} · sesi “{_e(sess.get('name',''))}”
             · {len(snap.get('drivers',[]))} pembalap</p>
          <table><thead><tr><th>#</th><th>Pembalap</th><th>Mobil</th><th>Lap</th>
          <th>Best</th><th>Terakhir</th><th></th></tr></thead><tbody>{''.join(rows)}</tbody></table>
        </section>""")
    html = f"""<!doctype html><html lang=id><meta charset=utf-8>
    <meta http-equiv=refresh content=5>
    <title>Hasil Live — YurLaps</title>
    <style>
      body{{background:#0b0f14;color:#e8eef7;font-family:Segoe UI,system-ui,sans-serif;padding:24px;max-width:1000px;margin:auto}}
      h1{{font-size:20px;letter-spacing:.5px}} h2{{font-size:18px;margin-bottom:2px}}
      .sub{{color:#8ba0b8;font-size:13px;margin-top:0}}
      table{{width:100%;border-collapse:collapse;margin-top:10px;font-variant-numeric:tabular-nums}}
      th{{text-align:left;font-size:11px;letter-spacing:1px;text-transform:uppercase;color:#5d7086;border-bottom:1px solid #243040;padding:8px}}
      td{{padding:9px 8px;border-bottom:1px solid #16202c;font-size:16px}}
      .best{{color:#ffd54a;font-weight:700}} section{{margin-bottom:34px}}
      .empty{{color:#5d7086}}
    </style>
    <h1>Hasil Live — YurLaps</h1>
    {''.join(parts) or '<p class=empty>Belum ada instance yang mengirim data.</p>'}
    <p class=sub style="margin-top:24px">Halaman ini memuat ulang tiap 5 detik.</p>
    """
    return Response(html, mimetype="text/html")


def _e(s) -> str:
    from markupsafe import escape
    return str(escape(s or ""))


def _fmt(ms) -> str:
    if not ms:
        return "—"
    ms = int(ms)
    return f"{ms // 60000}:{(ms // 1000) % 60:02d}.{ms % 1000:03d}"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "9000")))
