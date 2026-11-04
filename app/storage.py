"""Penyimpanan lokal (SQLite) untuk YurLaps.

Semua data milik pengguna, tersimpan di folder data aplikasi:

    sessions   : satu baris per sesi latihan
    drivers    : data personal transponder (nama, mobil, kelas)
    stints     : satu baris per stint (otomatis dibuka/ditutup)
    laps       : satu baris per lap (waktu, RSSI, MER, dsb)

Cara pakai dari Python lain:

    from app.storage import Storage
    db = Storage()
    db.export_session_csv(1, "hasil.csv")
"""
from __future__ import annotations

import csv
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from .config import default_data_dir

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS sessions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL,
    track             TEXT,
    event             TEXT,
    started_at        REAL NOT NULL,
    ended_at          REAL
);

CREATE TABLE IF NOT EXISTS drivers (
    transponder_id    INTEGER PRIMARY KEY,
    transponder_type  TEXT,
    driver            TEXT,
    car               TEXT,
    class_name        TEXT,
    note              TEXT,
    updated_at        REAL
);

CREATE TABLE IF NOT EXISTS stints (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id        INTEGER NOT NULL,
    transponder_id    INTEGER NOT NULL,
    transponder_type  TEXT,
    stint_index       INTEGER,
    started_at        REAL NOT NULL,
    ended_at          REAL,
    lap_count         INTEGER DEFAULT 0,
    best_ms           INTEGER,
    UNIQUE(id)
);

CREATE TABLE IF NOT EXISTS laps (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id        INTEGER NOT NULL,
    stint_id          INTEGER NOT NULL,
    transponder_id    INTEGER NOT NULL,
    driver            TEXT,
    seq               INTEGER,
    time_ms           INTEGER NOT NULL,
    at                REAL NOT NULL,
    rssi              REAL,
    mer               REAL,
    pass_duration_us  INTEGER
);

CREATE INDEX IF NOT EXISTS idx_laps_stint ON laps(stint_id);
CREATE INDEX IF NOT EXISTS idx_laps_session ON laps(session_id);
CREATE INDEX IF NOT EXISTS idx_stints_driver ON stints(transponder_id, session_id);
"""


class Storage:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else default_data_dir() / "yurlaps.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # -- sesi --------------------------------------------------------------
    def open_session(self, name: str, track: str, event: str, started_at: float) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO sessions(name, track, event, started_at) VALUES (?,?,?,?)",
                (name, track, event, started_at),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def close_session_and_open(self, sid: int, name: str, track: str, event: str,
                               started_at: float) -> int:
        with self._lock:
            self._conn.execute("UPDATE sessions SET ended_at=? WHERE id=? AND ended_at IS NULL",
                               (started_at, sid))
            self._conn.commit()
        return self.open_session(name, track, event, started_at)

    def sessions(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # -- driver ------------------------------------------------------------
    def save_driver(self, d) -> None:
        import time as _t
        with self._lock:
            self._conn.execute(
                """INSERT INTO drivers(transponder_id, transponder_type, driver, car,
                                       class_name, note, updated_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(transponder_id) DO UPDATE SET
                     transponder_type=excluded.transponder_type,
                     driver=excluded.driver, car=excluded.car,
                     class_name=excluded.class_name, note=excluded.note,
                     updated_at=excluded.updated_at""",
                (d.transponder_id, d.transponder_type, d.driver, d.car,
                 d.class_name, d.note, _t.time()),
            )
            self._conn.commit()

    def delete_driver(self, transponder_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM drivers WHERE transponder_id=?",
                               (int(transponder_id),))
            self._conn.commit()

    def drivers(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM drivers ORDER BY driver").fetchall()
        return [dict(r) for r in rows]

    # -- stint -------------------------------------------------------------
    def create_stint(self, st, d) -> int:
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO stints(session_id, transponder_id, transponder_type,
                                      stint_index, started_at, ended_at)
                   VALUES (?,?,?,?,?,?)""",
                (st.session_id, st.transponder_id, st.transponder_type,
                 st.index, st.started_at, st.ended_at),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def close_stint(self, st) -> None:
        with self._lock:
            best = st.best_lap.time_ms if st.best_lap else None
            self._conn.execute(
                "UPDATE stints SET ended_at=?, lap_count=?, best_ms=? WHERE id=?",
                (st.ended_at, st.lap_count, best, st.id),
            )
            self._conn.commit()

    def recover_open_stints(self, session_id: int) -> list[dict]:
        """Stint tanpa ended_at di sesi yang sama (aplikasi ditutup paksa)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM stints WHERE session_id=? AND ended_at IS NULL",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- lap ---------------------------------------------------------------
    def save_lap(self, st, lap, d) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO laps(session_id, stint_id, transponder_id, driver, seq,
                                    time_ms, at, rssi, mer, pass_duration_us)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (st.session_id, st.id, d.transponder_id, d.driver, lap.seq,
                 lap.time_ms, lap.at, lap.rssi, lap.mer, lap.pass_duration_us),
            )
            best = st.best_lap.time_ms if st.best_lap else None
            self._conn.execute(
                "UPDATE stints SET lap_count=?, best_ms=? WHERE id=?",
                (st.lap_count, best, st.id),
            )
            self._conn.commit()

    # -- ekspor ------------------------------------------------------------
    def session_rows(self, session_id: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT l.*, s.stint_index, s.transponder_type
                   FROM laps l LEFT JOIN stints s ON s.id = l.stint_id
                   WHERE l.session_id=? ORDER BY l.at""",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def export_session_csv(self, session_id: int, path) -> Path:
        path = Path(path)
        rows = self.session_rows(session_id)
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["waktu", "transponder_id", "tipe", "pembalap", "stint",
                        "lap_ke", "waktu_lap_ms", "waktu_lap", "rssi", "mer",
                        "pass_duration_us"])
            for r in rows:
                ms = r["time_ms"]
                w.writerow([
                    _iso(r["at"]), r["transponder_id"], r["transponder_type"] or "",
                    r["driver"] or "", r["stint_index"] or "", r["seq"] or "",
                    ms, _fmt(ms), r["rssi"], r["mer"], r["pass_duration_us"],
                ])
        return path

    def stats(self) -> dict:
        with self._lock:
            laps = self._conn.execute("SELECT COUNT(*) c FROM laps").fetchone()["c"]
            stints = self._conn.execute("SELECT COUNT(*) c FROM stints").fetchone()["c"]
            drivers = self._conn.execute("SELECT COUNT(*) c FROM drivers").fetchone()["c"]
        return {"laps": laps, "stints": stints, "drivers": drivers, "db": str(self.path)}

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.commit()
                self._conn.close()
            except Exception:
                pass


def _fmt(ms: int) -> str:
    ms = int(ms or 0)
    m, rem = divmod(ms, 60000)
    s, milli = divmod(rem, 1000)
    return f"{m}:{s:02d}.{milli:03d}"


def _iso(ts: float) -> str:
    import datetime as dt
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
