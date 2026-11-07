"""Automatic practice sessions with a frozen driver identity per transponder."""
from __future__ import annotations

import copy
import math
import statistics
import threading
import time
import uuid


def practice_stats(laps, passings=()):
    ordered = sorted(laps)
    average = statistics.mean(laps) if laps else None
    deviation = statistics.pstdev(laps) if laps else None
    speeds = [p["speed_kmh"] for p in passings if p.get("speed_kmh") is not None]
    return {
        "n_lap": len(laps), "lap_count": len(laps),
        "total_ms": sum(laps) if laps else None,
        "best_ms": ordered[0] if ordered else None,
        "last_ms": laps[-1] if laps else None,
        "average_ms": average, "sigma_ms": deviation,
        "top3_ms": statistics.mean(ordered[:3]) if len(laps) >= 3 else None,
        "consecutive_ms": min((statistics.mean(laps[i:i + 3])
                               for i in range(len(laps) - 2)), default=None),
        "p80_ms": statistics.mean(ordered[:math.ceil(len(laps) * .8)]) if laps else None,
        "consistency": round(max(0, 1 - deviation / average) * 100, 1) if average else None,
        "top_speed_kmh": max(speeds) if speeds else None,
        "average_speed_kmh": statistics.mean(speeds) if speeds else None,
    }


class PracticeRecorder:
    """Record one session per transponder while the race engine is idle.

    The first passing is the timing origin. Rejected passings never move it.
    Account/name and track settings are copied at that origin, so historical
    sessions cannot change owner when a transponder is transferred later.
    """

    def __init__(self, on_close=None, idle_seconds=120):
        self.lock = threading.RLock()
        self.active = False
        self.session_id = None
        self.track_id = None
        self.track_name = ""
        self.started_at = None
        self.min_lap_ms = 1500
        self.idle_seconds = max(1, float(idle_seconds))
        self.on_close = on_close
        self.history = []
        self._track = {}
        self._sessions = {}
        self._pending_close = {}

    @staticmethod
    def _now(now):
        return time.time() if now is None else float(now)

    @staticmethod
    def _track_signature(track):
        return tuple((track or {}).get(key) for key in
                     ("id", "name", "min_lap_ms", "loop_width_m"))

    def tick(self, *, race_status, track=None, now=None, idle_seconds=None):
        with self.lock:
            at = self._now(now)
            if idle_seconds is not None:
                self.idle_seconds = max(1, float(idle_seconds))
            eligible = race_status == "idle" and bool((track or {}).get("id"))
            changed_track = self._track_signature(track) != self._track_signature(self._track)
            if self.active and (not eligible or changed_track):
                self.close_all("track_changed" if changed_track else "race_started", now=at)
                self.active = False
            if eligible and not self.active:
                self.active = True
                self.session_id = str(uuid.uuid4())
                self._track = copy.deepcopy(track or {})
                self.track_id = self._track.get("id")
                self.track_name = self._track.get("name") or ""
                self.min_lap_ms = max(1, int(self._track.get("min_lap_ms") or 1500))
                self.started_at = at
            for key, session in list(self._sessions.items()):
                if at - session["last_passing_at"] >= self.idle_seconds:
                    self._close_driver(key, "idle", at)
            self._flush_closed()

    def _identity(self, transponder, member):
        member = member or {}
        account = member.get("user_id") or member.get("driver_id") or member.get("id")
        try:
            account = str(uuid.UUID(str(account))) if account else None
        except (TypeError, ValueError, AttributeError):
            account = None
        details = next((row for row in member.get("transponder_details", [])
                        if str(row.get("transponder_id")) == str(transponder)), {})
        name = member.get("name") or member.get("full_name") or f"TP {transponder}"
        nick = member.get("nick") or member.get("nickname") or ""
        return {
            "driver_id": account, "user_id": account, "member_id": member.get("id"),
            "identity_captured": True, "transponder": int(transponder),
            "name": name, "driver": name, "nick": nick, "nickname": nick,
            "tts_name": member.get("tts_name") or "",
            "avatar_url": member.get("avatar_url") or "",
            "class": details.get("rc_class") or member.get("kelas") or "",
            "car": details.get("rc_brand") or member.get("mobil") or "",
            "laps_ms": [], "passings": [],
        }

    def record(self, transponder, decoder_ts, *, member=None, rssi=None,
               hit_count=None, dur_us=None, pass_duration_us=None, now=None):
        with self.lock:
            at = self._now(now)
            def reject(reason):
                return {"accepted": False, "diterima": False, "reason": reason, "alasan": reason}
            if not self.active:
                return reject("Practice tidak aktif")
            if isinstance(decoder_ts, bool) or not isinstance(decoder_ts, int) or decoder_ts < 0:
                return reject("Timestamp decoder tidak sah")
            try:
                transponder = int(transponder)
            except (ValueError, TypeError):
                return reject("ID transponder tidak sah")
            if transponder < 0 or transponder > 4294967295:
                return reject("ID transponder tidak sah")
            key = str(transponder)
            session = self._sessions.get(key)
            if session and not session["driver"].get("user_id") and member:
                resolved = self._identity(transponder, member)
                if resolved.get("user_id"):
                    # Never attach earlier anonymous laps to today's owner.
                    # A newly resolved account begins from a fresh passing.
                    self._close_driver(key, "identity_resolved", at)
                    session = None
            if session and at - session["last_passing_at"] >= self.idle_seconds:
                self._close_driver(key, "idle", at)
                session = None
            previous = session["last_decoder_ts"] if session else None
            lap = decoder_ts - previous if previous is not None else None
            if lap is not None and lap <= 0:
                return reject("Timestamp duplikat atau terlambat")
            if lap is not None and lap < self.min_lap_ms:
                return reject("Di bawah minimum lap track")
            if session is None:
                session = {
                    "id": str(uuid.uuid4()), "track_id": self.track_id,
                    "track_name": self.track_name,
                    "loop_width_m": self._track.get("loop_width_m"),
                    "min_lap_ms": self.min_lap_ms, "started_at": at,
                    "ended_at": None, "last_passing_at": at,
                    "last_decoder_ts": decoder_ts, "visibility": "private",
                    "driver": self._identity(transponder, copy.deepcopy(member)),
                }
                self._sessions[key] = session
            duration = dur_us if dur_us is not None else pass_duration_us
            width = session.get("loop_width_m")
            speed = None
            if width and duration and float(width) > 0 and float(duration) > 0:
                value = float(width) * 3_600_000 / float(duration)
                speed = round(value, 2) if math.isfinite(value) else None
            driver = session["driver"]
            if lap is not None:
                driver["laps_ms"].append(lap)
            driver["passings"].append({
                "seq": len(driver["laps_ms"]), "at": at,
                "decoder_timecode": decoder_ts, "lap_ms": lap,
                "origin": lap is None, "rssi": rssi, "hit_count": hit_count,
                "pass_duration_us": duration, "speed_kmh": speed,
            })
            session["last_decoder_ts"] = decoder_ts
            session["last_passing_at"] = at
            return {"accepted": True, "diterima": True, "origin": lap is None,
                    "lap_ms": lap, "session_id": session["id"],
                    "driver": copy.deepcopy(driver)}

    def _payload(self, session):
        driver = copy.deepcopy(session["driver"])
        driver.update(practice_stats(driver["laps_ms"], driver["passings"]))
        return {
            **{key: copy.deepcopy(value) for key, value in session.items()
               if key not in ("driver", "last_decoder_ts")},
            "schema_version": 2, "identity_captured": True,
            "laps": {str(driver["transponder"]): list(driver["laps_ms"])},
            "drivers": [driver], "summary": practice_stats(driver["laps_ms"], driver["passings"]),
        }

    def _close_driver(self, key, reason, at):
        session = self._sessions.pop(key, None)
        if session is None or not session["driver"]["laps_ms"]:
            return None
        session["ended_at"] = session["last_passing_at"]
        session["closed_at"] = at
        session["close_reason"] = reason
        closed = self._payload(session)
        self.history.insert(0, closed)
        del self.history[500:]
        if self.on_close:
            self._pending_close[closed["id"]] = closed
            self._flush_closed()
        return copy.deepcopy(closed)

    def _flush_closed(self):
        for key, session in list(self._pending_close.items()):
            try:
                self.on_close(copy.deepcopy(session))
            except Exception:
                continue
            self._pending_close.pop(key, None)

    def close_all(self, reason="closed", *, now=None):
        with self.lock:
            at = self._now(now)
            return [closed for key in list(self._sessions)
                    if (closed := self._close_driver(key, reason, at)) is not None]

    def clock_reset(self, *, now=None):
        return self.close_all("decoder_interrupted", now=now)

    def snapshot(self):
        with self.lock:
            sessions = [self._payload(session) for session in self._sessions.values()]
            drivers = [driver for session in sessions for driver in session["drivers"]]
            return {
                "active": self.active, "idle_seconds": self.idle_seconds,
                "active_sessions": sessions,
                "session": {
                    "id": self.session_id, "track_id": self.track_id,
                    "track_name": self.track_name, "started_at": self.started_at,
                    "laps": {str(d["transponder"]): list(d["laps_ms"]) for d in drivers},
                    "drivers": drivers, "visibility": "private",
                } if self.session_id else None,
                "history": copy.deepcopy(self.history),
                "pending_persistence": len(self._pending_close),
            }

    def set_visibility(self, visibility):
        raise ValueError("Privasi practice diatur dari dashboard website pemilik akun")
