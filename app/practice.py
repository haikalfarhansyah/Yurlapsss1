"""Automatic idle practice recorder.

Practice is deliberately separate from Quick Race: it records decoder laps while
the app is idle, pauses during countdown/live race, and keeps a compact local
history that can be uploaded when an account is linked.
"""
from __future__ import annotations

import threading
import time
import uuid


class PracticeRecorder:
    def __init__(self, on_close=None):
        self.lock = threading.RLock()
        self.active = False
        self.session_id = None
        self.track_id = None
        self.track_name = ""
        self.started_at = None
        self.laps = {}
        self.last_ts = {}
        self.history = []
        self.visibility = "private"
        self.min_lap_ms = 1500
        self.on_close = on_close

    def tick(self, *, race_status, track=None, now=None):
        with self.lock:
            idle = race_status == "idle"
            if idle and not self.active:
                self.active = True
                self.session_id = str(uuid.uuid4())
                self.track_id = (track or {}).get("id")
                self.track_name = (track or {}).get("name", "")
                self.min_lap_ms = int((track or {}).get("min_lap_ms") or 1500)
                self.started_at = now or time.time()
                self.laps = {}
                self.last_ts = {}
            elif not idle and self.active:
                self._close(now or time.time())

    def record(self, transponder, decoder_ts):
        with self.lock:
            if not self.active or decoder_ts is None:
                return
            previous = self.last_ts.get(str(transponder))
            self.last_ts[str(transponder)] = int(decoder_ts)
            if previous is None:
                return
            lap = int(decoder_ts) - previous
            if self.min_lap_ms <= lap < 600000:
                self.laps.setdefault(str(transponder), []).append(lap)

    def _close(self, ended_at):
        closed = None
        if self.session_id:
            closed = {
                "id": self.session_id, "track_id": self.track_id,
                "track_name": self.track_name, "started_at": self.started_at,
                "ended_at": ended_at, "laps": self.laps,
                "visibility": self.visibility,
            }
            self.history.insert(0, closed)
            del self.history[50:]
        self.active = False
        if closed and self.on_close and any(closed.get("laps", {}).values()):
            try:
                self.on_close(dict(closed))
            except Exception:
                # Timing must continue even if local persistence is unavailable.
                pass
        return closed

    def snapshot(self):
        with self.lock:
            return {"active": self.active, "session": {
                "id": self.session_id, "track_id": self.track_id,
                "track_name": self.track_name, "started_at": self.started_at,
                "laps": self.laps, "visibility": self.visibility,
            } if self.session_id else None, "history": list(self.history)}

    def set_visibility(self, visibility):
        if visibility not in ("private", "public"):
            raise ValueError("visibility harus private atau public")
        with self.lock:
            self.visibility = visibility
            if self.session_id:
                for row in self.history:
                    if row.get("id") == self.session_id:
                        row["visibility"] = visibility
                if self.active:
                    # snapshot reads this value for the UI; persistence is done by the server.
                    pass
