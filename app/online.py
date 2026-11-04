"""Sinkronisasi ke website hasil online (opsional).

YurLaps mengirim snapshot ringkas ke endpoint HTTP apa pun yang menerima JSON:

    POST {online_url}/api/stintlab/ingest
    Authorization: Bearer <online_token>
    Content-Type: application/json

Body (contoh):
    {
      "instance": "Sirkuit Latihan", "track": "...", "event": "...",
      "session": {"id": 12, "name": "Practice Day", "started_at": 1762...},
      "sent_at": 1762...,
      "drivers": [{"transponder_id": 1615544, "driver": "Budi", "car": "Xray X4",
                   "class_name": "Mod", "total_laps": 24, "best_ms": 9412,
                   "last_ms": 9503, "stint_index": 2, "stint_open": true, ...}]
    }

Selain PUSH dari YurLaps (mode default), tersedia mode PULL: website cukup
mengambil ``GET {online_url}/api/state`` dari komputer kamu (butuh port yang
bisa dijangkau, mis. lewat tunnel Cloudflare/ngrok). Aktifkan dengan
``cfg.online_mode = "pull"`` — tidak ada data yang dikirim keluar sama sekali.

Server contoh (Flask/FastAPI/Node/PHP) ada di tools/ingest_server_example.py.
Kegagalan jaringan tidak pernah mengganggu timing: error hanya dicatat di log
dan dicoba lagi pada interval berikutnya.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

from .engine import Engine


class OnlineSync(threading.Thread):
    def __init__(self, engine: Engine):
        super().__init__(name="online-sync", daemon=True)
        self.engine = engine
        self._stop = threading.Event()
        self.last_ok: Optional[float] = None
        self.last_error: Optional[str] = None
        self.fail_count = 0
        self._last_version = -1

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------
    def run(self) -> None:
        while not self._stop.is_set():
            cfg = self.engine.cfg
            interval = max(1.0, float(cfg.sync_interval or 2.0))
            mode = (getattr(cfg, "online_mode", "push") or "push").lower()
            if cfg.online_enabled and cfg.online_url.strip() and mode == "push":
                if self.engine.version != self._last_version:
                    self._last_version = self.engine.version
                    self.push_once()
            self._stop.wait(interval)

    # ------------------------------------------------------------------
    def push_once(self) -> bool:
        cfg = self.engine.cfg
        url = cfg.online_url.strip().rstrip("/")
        if not url:
            return False
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        endpoint = url + "/api/stintlab/ingest"
        body = json.dumps(self.engine.online_payload()).encode("utf-8")
        req = urllib.request.Request(endpoint, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "YurLaps/0.1")
        if cfg.online_token:
            req.add_header("Authorization", f"Bearer {cfg.online_token}")
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                resp.read(512)
            self.last_ok = time.time()
            self.fail_count = 0
            self.last_error = None
            return True
        except urllib.error.HTTPError as exc:
            self.fail_count += 1
            self.last_error = f"HTTP {exc.code}"
        except Exception as exc:
            self.fail_count += 1
            self.last_error = str(exc)
        if self.fail_count in (1, 5, 20):
            self.engine.log("warn", f"Sinkronisasi online gagal: {self.last_error}")
        return False
