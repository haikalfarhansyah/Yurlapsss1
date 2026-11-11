"""Penyimpanan data club untuk YurLaps mode offline.

Modul ini sengaja **tidak menyentuh jaringan sama sekali**. Semua data club
(track, member, preset) dan semua hasil balapan tersimpan di SQLite lokal.
Saat fitur daring dinyalakan nanti, modul ini tetap menjadi sumber kebenaran
untuk lap mentah; yang ditambahkan hanya lapisan pengunggah.

Kenapa berkas sendiri (``yurlaps_club.sqlite3``), bukan menyatu dengan
``yurlaps.sqlite3`` milik mesin timing? Supaya kerangka club bisa dipakai dan
diuji tanpa menyalakan decoder, dan supaya migrasi bentuk tabel club tidak
pernah berisiko ke data lap mesin lama.

Aturan yang dipegang:
  * ``SCHEMA_VERSION`` dinaikkan setiap bentuk tabel berubah.
  * Migrasi memakai ``BEGIN IMMEDIATE`` + ``ROLLBACK`` eksplisit (lihat
    catatan di ``migrate()``: pola ``with conn:`` meninggalkan kolom setengah
    jadi kalau proses mati di tengah jalan).
  * Hanya ``ADD COLUMN``; tidak ada DROP/RENAME tanpa persetujuan.
"""
from __future__ import annotations

import json
import datetime
import functools
import math
import uuid
import threading
import sqlite3
import time
from pathlib import Path
from typing import Optional

from .config import default_data_dir

SCHEMA_VERSION = 9

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS club (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    club_id     TEXT,
    name        TEXT NOT NULL,
    role        TEXT DEFAULT 'leader',
    synced_at   REAL
);

CREATE TABLE IF NOT EXISTS track (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    addr        TEXT,
    width_m     REAL,                 -- legacy track length; never used for speed
    note        TEXT,
    best_ms     INTEGER,
    holder      TEXT,
    min_lap_ms  INTEGER NOT NULL DEFAULT 1500,
    active      INTEGER NOT NULL DEFAULT 0,
    synced      INTEGER DEFAULT 0,    -- 0 belum sinkron ke web
    updated_at  REAL
);

CREATE TABLE IF NOT EXISTS member (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,        -- nama lengkap
    nick        TEXT,                 -- nama panggilan
    kelas       TEXT,
    mobil       TEXT,
    role        TEXT DEFAULT 'member',
    status      TEXT DEFAULT 'active',
    transponder TEXT,                 -- JSON array nomor transponder
    tts_name    TEXT,
    avatar_url  TEXT,
    updated_at  REAL
);

CREATE TABLE IF NOT EXISTS observed_transponder (
    transponder TEXT PRIMARY KEY,
    observed_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS preset (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    target      TEXT DEFAULT 'time',  -- time | laps
    value       INTEGER DEFAULT 180,
    rank        TEXT DEFAULT 'lapsTime',
    start_mode  TEXT DEFAULT 'grid',
    cd          INTEGER DEFAULT 5,
    sound       INTEGER DEFAULT 1,
    random_delay_max INTEGER NOT NULL DEFAULT 1,
    target_time_ms INTEGER,
    finish_current_lap INTEGER NOT NULL DEFAULT 0,
    stagger_mode TEXT NOT NULL DEFAULT 'manual',
    stagger_gap_ms INTEGER NOT NULL DEFAULT 10000,
    updated_at  REAL
);

CREATE TABLE IF NOT EXISTS race (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    track_id    TEXT,
    track_name  TEXT,
    track_width REAL,
    cfg         TEXT,                 -- JSON pengaturan sesi
    started_at  REAL NOT NULL,
    ended_at    REAL,
    status      TEXT DEFAULT 'saved', -- saved | live | discard
    sync        TEXT DEFAULT 'belum', -- belum | terkirim
    remote_id   TEXT
);

CREATE TABLE IF NOT EXISTS race_driver (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id     TEXT NOT NULL REFERENCES race(id) ON DELETE CASCADE,
    pos         INTEGER,
    uid         TEXT,
    name        TEXT,
    nick        TEXT,
    kelas       TEXT,
    transponder INTEGER,
    luar        INTEGER DEFAULT 0,
    best_ms     INTEGER,
    dur_us      INTEGER,
    kmh         INTEGER
);

CREATE TABLE IF NOT EXISTS race_lap (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id     TEXT NOT NULL REFERENCES race(id) ON DELETE CASCADE,
    transponder INTEGER NOT NULL,
    seq         INTEGER,
    lap_ms      INTEGER NOT NULL,
    at          REAL NOT NULL,
    rssi        REAL,
    dur_us      INTEGER,
    skip        INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS practice_session (
    id          TEXT PRIMARY KEY,
    club_id     TEXT,
    track_id    TEXT,
    track_name  TEXT NOT NULL DEFAULT '',
    started_at  REAL NOT NULL,
    ended_at    REAL,
    visibility  TEXT NOT NULL DEFAULT 'private',
    sync        TEXT NOT NULL DEFAULT 'belum',
    result_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_lap_race ON race_lap (race_id, transponder);
CREATE INDEX IF NOT EXISTS idx_rd_race  ON race_driver (race_id);
CREATE TABLE IF NOT EXISTS race_deletion (
    id TEXT PRIMARY KEY,
    club_id TEXT NOT NULL,
    remote_id TEXT,
    deleted_at REAL NOT NULL
);
"""

def locked(method):
    @functools.wraps(method)
    def call(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return call


class ClubStore:
    """Akses SQLite untuk club, track, member, preset, dan hasil balapan."""

    def __init__(self, path: Optional[Path] = None):
        self.lock = threading.RLock()
        self.path = Path(path) if path else default_data_dir() / "yurlaps_club.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(SCHEMA)
        self.migrate()
        self._seed()

    # -- migrasi ---------------------------------------------------------
    @locked
    def migrate(self) -> int:
        conn = self._conn
        v = conn.execute("PRAGMA user_version").fetchone()[0]
        if v >= SCHEMA_VERSION:
            return 0

        # "with conn:" TIDAK cukup: dengan isolation_level bawaan, sqlite3 hanya
        # membuka transaksi untuk DML, sedangkan ALTER TABLE berjalan di
        # autocommit -> kalau proses mati di tengah migrasi, kolom setengah
        # jadi tertinggal. Karena itu transaksi dikendalikan manual.
        lama = conn.isolation_level
        conn.isolation_level = None
        dibuat = 0
        try:
            conn.execute("BEGIN IMMEDIATE")
            if v < 1:
                # bentuk awal; tabel sudah dibuat oleh SCHEMA
                pass
            # PR #2 and older cloud prototypes both used version 2 differently.
            # Inspect columns rather than assuming what a version number means.
            additions = {
                "track": {"loop_width_m": "REAL", "min_lap_ms": "INTEGER NOT NULL DEFAULT 1500",
                          "active": "INTEGER NOT NULL DEFAULT 0", "remote_revision": "INTEGER DEFAULT 0",
                          "mutation_id": "TEXT", "deleted": "INTEGER DEFAULT 0", "sync_error": "TEXT"},
                "race": {"club_id": "TEXT", "result_json": "TEXT"},
                "preset": {"remote_revision": "INTEGER DEFAULT 0", "mutation_id": "TEXT",
                           "deleted": "INTEGER DEFAULT 0", "sync_error": "TEXT", "synced": "INTEGER DEFAULT 0",
                           "countdown_step_ms": "INTEGER NOT NULL DEFAULT 1500",
                           "random_delay_max": "INTEGER NOT NULL DEFAULT 2", "target_time_ms": "INTEGER",
                           "finish_current_lap": "INTEGER NOT NULL DEFAULT 0", "stagger_mode": "TEXT NOT NULL DEFAULT 'manual'",
                           "stagger_gap_ms": "INTEGER NOT NULL DEFAULT 10000"},
                "member": {"source": "TEXT DEFAULT 'local'", "tts_name": "TEXT", "avatar_url": "TEXT",
                           "transponder_details": "TEXT DEFAULT '[]'"},
                "practice_session": {"club_id": "TEXT", "track_id": "TEXT",
                           "track_name": "TEXT NOT NULL DEFAULT ''", "started_at": "REAL",
                           "ended_at": "REAL", "visibility": "TEXT NOT NULL DEFAULT 'private'",
                           "sync": "TEXT NOT NULL DEFAULT 'belum'", "result_json": "TEXT NOT NULL DEFAULT '{}'",
                           "revision": "INTEGER NOT NULL DEFAULT 1"},
            }
            for table, columns in additions.items():
                present = {r[1] for r in conn.execute("PRAGMA table_info(" + table + ")")}
                for column, definition in columns.items():
                    if column not in present:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            # Presets created by 0.4.2 used 1 as the old default. The current
            # contract starts the GO random delay at 2 seconds, so upgrade
            # legacy rows before the stricter validator reads them.
            conn.execute("UPDATE preset SET random_delay_max=2 WHERE random_delay_max IS NULL OR random_delay_max < 2")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_race_pending ON race(club_id, sync, status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_practice_pending ON practice_session(club_id, sync, started_at)")
            if conn.execute("SELECT COUNT(*) FROM track WHERE deleted=0 AND active=1").fetchone()[0] == 0:
                conn.execute("UPDATE track SET active=1 WHERE id=(SELECT id FROM track WHERE deleted=0 ORDER BY name COLLATE NOCASE LIMIT 1)")
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.isolation_level = lama
        return dibuat

    @locked
    def _seed(self) -> None:
        """Initialize only the local club metadata.

        Tracks, members, and presets are real user data and must never be
        fabricated on first launch. A blank local club row keeps the offline
        store usable until the operator signs in or creates a track.
        """
        c = self._conn
        if not c.execute("SELECT 1 FROM club WHERE id = 1").fetchone():
            c.execute("INSERT INTO club (id, club_id, name, role, synced_at) "
                      "VALUES (1, NULL, 'Belum terhubung', 'operator', NULL)")
        c.commit()

    # -- club ------------------------------------------------------------
    @locked
    def club(self) -> dict:
        r = self._conn.execute("SELECT * FROM club WHERE id = 1").fetchone()
        return dict(r) if r else {}

    @locked
    def set_club(self, **kw) -> None:
        kolom = [k for k in ("club_id", "name", "role", "synced_at") if k in kw]
        if not kolom:
            return
        sets = ", ".join(f"{k} = ?" for k in kolom)
        self._conn.execute(f"UPDATE club SET {sets} WHERE id = 1",
                           [kw[k] for k in kolom])
        self._conn.commit()

    # -- track -----------------------------------------------------------
    @locked
    def tracks(self) -> list[dict]:
        """Daftar track + jumlah sesi per track.

        ``n_race`` dihitung di sini (bukan di UI) supaya angka "0 sesi di track
        ini" tidak pernah salah walaupun daftar riwayat belum dimuat.
        """
        rows = self._conn.execute(
            "SELECT t.*, (SELECT COUNT(*) FROM race r WHERE r.track_id = t.id)"
            " AS n_race FROM track t WHERE t.deleted=0"
            " ORDER BY t.active DESC, t.synced ASC, t.name COLLATE NOCASE").fetchall()
        return [dict(r) for r in rows]

    @locked
    def track(self, track_id: str) -> Optional[dict]:
        r = self._conn.execute(
            "SELECT t.*, (SELECT COUNT(*) FROM race r WHERE r.track_id = t.id)"
            " AS n_race FROM track t WHERE t.deleted=0 AND t.id = ?", (track_id,)).fetchone()
        return dict(r) if r else None

    @locked
    def save_track(self, data: dict) -> dict:
        """Tambah atau ubah track. ``id`` kosong -> buat baru."""
        now = time.time()
        tid = (data.get("id") or "").strip()
        nama = (data.get("name") or "").strip()
        if not nama:
            raise ValueError("Nama track wajib diisi")
        try:
            lebar = float(data["width_m"]) if data.get("width_m") not in (None, "") else None
        except (TypeError, ValueError):
            raise ValueError("Lebar loop harus angka")
        if lebar is not None and lebar <= 0:
            raise ValueError("Lebar loop harus lebih dari 0")
        raw_min = data.get("min_lap_ms")
        if raw_min in (None, "") and data.get("min_lap_s") not in (None, ""):
            raw_min = float(data.get("min_lap_s")) * 1000
        try:
            min_lap_ms = int(round(float(raw_min))) if raw_min not in (None, "") else 1500
        except (TypeError, ValueError):
            raise ValueError("Minimum waktu lap harus angka dalam detik")
        if min_lap_ms < 100:
            raise ValueError("Minimum waktu lap harus minimal 0,1 detik")
        active = 1 if data.get("active") in (True, 1, "1", "true", "on") else 0

        if tid and self.track(tid):
            self._conn.execute(
                "UPDATE track SET name=?, addr=?, width_m=?, note=?, min_lap_ms=?, active=?, synced=0,"
                " updated_at=? WHERE id=?",
                (nama, data.get("addr") or "", lebar, data.get("note") or "", min_lap_ms, active, now, tid))
        else:
            tid = tid or str(uuid.uuid4())
            self._conn.execute(
                "INSERT INTO track (id, name, addr, width_m, note, best_ms, holder,"
                " synced, updated_at, min_lap_ms, active) VALUES (?,?,?,?,?,NULL,NULL,0,?,?,?)",
                (tid, nama, data.get("addr") or "", lebar, data.get("note") or "", now, min_lap_ms, active))
        width = data.get("loop_width_m")
        try:
            width = float(width) if width not in (None, "") else None
        except (TypeError, ValueError):
            self._conn.rollback()
            raise ValueError("Jarak antarkabel loop harus angka dalam meter")
        if width is not None and (not math.isfinite(width) or width <= 0):
            self._conn.rollback()
            raise ValueError("Jarak antarkabel loop harus lebih dari nol")
        self._conn.execute("UPDATE track SET loop_width_m=? WHERE id=?", (width, tid))
        if active:
            self._conn.execute("UPDATE track SET active=0 WHERE id<>?", (tid,))
        elif self._conn.execute("SELECT COUNT(*) FROM track WHERE deleted=0 AND active=1").fetchone()[0] == 0:
            self._conn.execute("UPDATE track SET active=1 WHERE id=?", (tid,))
        self._conn.execute("UPDATE track SET mutation_id=?, sync_error=NULL WHERE id=?",
                           (str(uuid.uuid4()), tid))
        self._conn.commit()
        return self.track(tid) or {}

    @locked
    def delete_track(self, track_id: str) -> bool:
        was_active = self._conn.execute("SELECT active FROM track WHERE id=?", (track_id,)).fetchone()
        cur = self._conn.execute("UPDATE track SET deleted=1, active=0, synced=0, mutation_id=?, "
                                 "sync_error=NULL WHERE id=?", (str(uuid.uuid4()), track_id))
        if was_active and was_active[0]:
            self._conn.execute("UPDATE track SET active=1 WHERE id=(SELECT id FROM track WHERE deleted=0 ORDER BY name COLLATE NOCASE LIMIT 1)")
        self._conn.commit()
        return cur.rowcount > 0

    @locked
    def mark_tracks_synced(self) -> int:
        cur = self._conn.execute("UPDATE track SET synced = 1 WHERE synced = 0")
        self._conn.commit()
        return cur.rowcount

    @locked
    def perbarui_rekor(self, track_id: str, best_ms: Optional[int], holder: str) -> None:
        if best_ms is None:
            return
        t = self.track(track_id)
        if not t:
            return
        if not t.get("best_ms") or best_ms < t["best_ms"]:
            self._conn.execute("UPDATE track SET best_ms = ?, holder = ?, synced=0, mutation_id=? WHERE id = ?",
                               (best_ms, holder, str(uuid.uuid4()), track_id))
            self._conn.commit()

    # -- member ----------------------------------------------------------
    def member_for_transponder(self, transponder):
        priority = {"cloud": 0, "website": 0, "local_guest": 1, "local": 1, "guest": 2}
        return next((m for m in sorted(self.members(), key=lambda m: priority.get(m.get("source"), 3)) if m.get("status") == "active"
                     and str(transponder) in {str(t) for t in m.get("transponder", [])}), None)

    @locked
    def save_local_guest(self, transponder, name: str, tts_name: str,
                         nick: str = "", kelas: str = "", mobil: str = "") -> dict:
        """An observed transponder gets a local guest identity, never an account link."""
        tp = str(int(transponder))
        if tp not in self.observed_transponders():
            raise ValueError("Transponder belum terbaca. Gunakan Tangkap transponder.")
        name, tts_name = str(name or "").strip(), str(tts_name or "").strip()
        if not name or not tts_name:
            raise ValueError("Nama dan nama TTS wajib diisi")
        current = next((m for m in self.members() if tp in {str(x) for x in m["transponder"]}
                        and m.get("source") in ("cloud", "website")), None)
        if current:
            raise ValueError("Transponder sudah terdaftar pada member club")
        old = next((m for m in self.members() if tp in {str(x) for x in m["transponder"]}
                    and m.get("source") in ("local_guest", "local")), None)
        mid = old["id"] if old else "local-guest-" + uuid.uuid4().hex
        self._conn.execute(
            "INSERT INTO member (id,name,nick,kelas,mobil,role,status,transponder,updated_at,source,tts_name)"
            " VALUES (?,?,?,?,?,'guest','active',?,?,'local_guest',?)"
            " ON CONFLICT(id) DO UPDATE SET name=excluded.name,nick=excluded.nick,"
            " kelas=excluded.kelas,mobil=excluded.mobil,transponder=excluded.transponder,"
            " updated_at=excluded.updated_at,source='local_guest',tts_name=excluded.tts_name",
            (mid, name, str(nick or ""), str(kelas or ""), str(mobil or ""),
             json.dumps([int(tp)]), time.time(), tts_name))
        self._conn.commit()
        return next(m for m in self.members() if m["id"] == mid)

    @locked
    def resolve_local_guest(self, transponder, account_id: str) -> bool:
        """Remove a local alias only after operator accepts the account profile."""
        tp = str(int(transponder))
        candidate = next((m for m in self.members() if m["id"] == account_id
                          and m.get("source") in ("guest", "cloud")
                          and tp in {str(x) for x in m["transponder"]}), None)
        if not candidate:
            raise ValueError("Profil akun belum tersedia untuk transponder ini")
        aliases = [m["id"] for m in self.members() if m.get("source") in ("local_guest", "local")
                   and tp in {str(x) for x in m["transponder"]}]
        for mid in aliases:
            self._conn.execute("DELETE FROM member WHERE id=?", (mid,))
        self._conn.commit()
        return bool(aliases)

    @locked
    def delete_member(self, member_id: str) -> bool:
        """Delete only locally-created offline drivers.

        Website members are authoritative and are refreshed by member sync.
        """
        row = self._conn.execute("SELECT source FROM member WHERE id=?", (member_id,)).fetchone()
        if not row or row[0] in ("cloud", "website", "guest"):
            return False
        cur = self._conn.execute("DELETE FROM member WHERE id=?", (member_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def observe_transponder(self, transponder):
        self._conn.execute("INSERT INTO observed_transponder(transponder,observed_at) VALUES (?,?) "
                           "ON CONFLICT(transponder) DO UPDATE SET observed_at=excluded.observed_at",
                           (str(int(transponder)), time.time()))
        self._conn.commit()

    def observed_transponders(self):
        return [row[0] for row in self._conn.execute(
            "SELECT transponder FROM observed_transponder WHERE observed_at>? ORDER BY observed_at DESC LIMIT 100",
            (time.time()-86400,))]

    def save_member(self, data, *, _commit=True, _source=None):
        with self.lock:
            name = str(data.get("name") or "").strip()
            if not name:
                raise ValueError("Nama pembalap wajib diisi")
            mid = str(data.get("id") or "m-" + uuid.uuid4().hex)
            raw = data.get("transponder", [])
            if isinstance(raw, str):
                raw = raw.replace(",", " ").split()
            try:
                ids = [int(x) for x in raw]
            except (TypeError, ValueError):
                raise ValueError("ID transponder harus bilangan bulat")
            if any(x < 0 or x > 4294967295 for x in ids) or len(ids) != len(set(ids)):
                raise ValueError("ID transponder tidak sah atau ganda")
            members = self.members()
            for m in members:
                if m["id"] != mid and set(m["transponder"]) & set(ids):
                    raise ValueError("Transponder sudah terdaftar pada " + m["name"])
            old = next((m for m in members if m["id"] == mid), {})
            if old.get("source") in ("cloud", "guest"):
                raise ValueError("Modifikasi member akun dan transponder melalui website")
            source = old.get("source") or _source or "local"
            self._conn.execute(
                "INSERT INTO member (id,name,nick,kelas,mobil,role,status,transponder,updated_at,source,tts_name,avatar_url) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name,nick=excluded.nick,kelas=excluded.kelas,mobil=excluded.mobil,"
                "transponder=excluded.transponder,updated_at=excluded.updated_at,"
                "tts_name=excluded.tts_name,avatar_url=excluded.avatar_url",
                (mid,name,str(data.get("nick") or ""),str(data.get("kelas") or ""),
                 str(data.get("mobil") or ""),old.get("role", "member"),
                 old.get("status", "active"),json.dumps(ids),time.time(),source,
                 str(data.get("tts_name") or old.get("tts_name") or ""),
                 str(data.get("avatar_url") or old.get("avatar_url") or "")))
            if _commit:
                self._conn.commit()
            return next(m for m in self.members() if m["id"] == mid)

    def import_members(self, rows):
        if not isinstance(rows, list) or len(rows) > 2000:
            raise ValueError("Gunakan array JSON member, maksimal 2000 baris")
        identifiers = set()
        with self.lock, self._conn:
            for row in rows:
                if not isinstance(row, dict) or not row.get("id"):
                    raise ValueError("Setiap member impor harus memiliki id dan name")
                mid = "web-" + str(row["id"])
                if mid in identifiers:
                    raise ValueError("ID member ganda dalam berkas impor")
                identifiers.add(mid)
                self.save_member(dict(row, id=mid), _commit=False, _source="website")
        return len(rows)

    @locked
    def members(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM member ORDER BY status ASC, name COLLATE NOCASE").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["transponder"] = json.loads(d.get("transponder") or "[]")
            d["transponder_details"] = json.loads(d.get("transponder_details") or "[]")
            out.append(d)
        return out

    # -- preset ----------------------------------------------------------
    @locked
    def presets(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM preset WHERE deleted=0 ORDER BY rowid").fetchall()
        return [dict(r) for r in rows]

    @locked
    def preset(self, pid: str) -> Optional[dict]:
        r = self._conn.execute("SELECT * FROM preset WHERE deleted=0 AND id = ?", (pid,)).fetchone()
        return dict(r) if r else None

    @locked
    def save_preset(self, data: dict) -> dict:
        from .race_config import normalize
        now = time.time()
        pid = (data.get("id") or "").strip()
        existing = self.preset(pid) if pid else None
        cfg = normalize(data, existing or None)
        nama, target, nilai = cfg["name"], cfg["target"], cfg["value"]
        start, rank = cfg["start_mode"], cfg["rank"]
        cd, sound = cfg["cd"], int(cfg["sound"])
        random_delay_max = cfg["random_delay_max"]
        countdown_step_ms = cfg["countdown_step_ms"]
        target_time_ms = cfg["target_time_ms"]
        finish_current_lap = int(cfg["finish_current_lap"])
        stagger_mode = cfg["stagger_mode"]
        stagger_gap_ms = cfg["stagger_gap_ms"]

        if pid and self.preset(pid):
            self._conn.execute(
                "UPDATE preset SET name=?, target=?, value=?, rank=?, start_mode=?,"
                " cd=?, sound=?, countdown_step_ms=?, random_delay_max=?, target_time_ms=?, finish_current_lap=?,"
                " stagger_mode=?, stagger_gap_ms=?, updated_at=? WHERE id=?",
                (nama, target, nilai, rank, start, cd, sound, countdown_step_ms, random_delay_max, target_time_ms,
                 finish_current_lap, stagger_mode, stagger_gap_ms, now, pid))
        else:
            pid = pid or str(uuid.uuid4())
            self._conn.execute(
                "INSERT INTO preset (id, name, target, value, rank, start_mode, cd,"
                " sound, countdown_step_ms, random_delay_max, target_time_ms, finish_current_lap, stagger_mode, stagger_gap_ms, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, nama, target, nilai, rank, start, cd, sound, countdown_step_ms, random_delay_max, target_time_ms,
                 finish_current_lap, stagger_mode, stagger_gap_ms, now))
        self._conn.execute("UPDATE preset SET synced=0, mutation_id=?, sync_error=NULL WHERE id=?",
                           (str(uuid.uuid4()), pid))
        self._conn.commit()
        return self.preset(pid) or {}

    @locked
    def delete_preset(self, pid: str) -> bool:
        if self._conn.execute("SELECT COUNT(*) FROM preset WHERE deleted=0").fetchone()[0] <= 1:
            raise ValueError("Minimal satu preset harus ada")
        cur = self._conn.execute("UPDATE preset SET deleted=1, synced=0, mutation_id=?, "
                                 "sync_error=NULL WHERE id=?", (str(uuid.uuid4()), pid))
        self._conn.commit()
        return cur.rowcount > 0

    # -- practice -------------------------------------------------------
    @locked
    def simpan_practice(self, session: dict, *, club_id: Optional[str] = None) -> str:
        """Persist an automatic idle-practice session and its lap grouping.

        The payload keeps transponder identity and driver metadata together so
        an account link can be resolved later without changing old history.
        """
        sid = str(session.get("id") or uuid.uuid4())
        data = dict(session)
        data.pop("revision", None)
        data.pop("result_json", None)
        data.pop("sync", None)
        data.setdefault("schema_version", 1)
        encoded = json.dumps(data, ensure_ascii=False, sort_keys=True)
        old = self._conn.execute("SELECT result_json FROM practice_session WHERE id=?", (sid,)).fetchone()
        if old and old[0] == encoded:
            return sid
        self._conn.execute(
            "INSERT INTO practice_session"
            "(id,club_id,track_id,track_name,started_at,ended_at,visibility,sync,result_json)"
            "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "track_name=excluded.track_name, ended_at=excluded.ended_at, "
            "sync='belum', result_json=excluded.result_json, revision=practice_session.revision+1",
            (sid, club_id or self.club().get("club_id"), data.get("track_id"),
             data.get("track_name") or "", float(data.get("started_at") or time.time()),
             data.get("ended_at"), data.get("visibility") if data.get("visibility") in ("private", "public") else "private",
             "belum", encoded),
        )
        self._conn.commit()
        return sid

    @locked
    def practice_sessions(self, limit: int = 200, *, pending_only: bool = False) -> list[dict]:
        where = " WHERE sync != 'terkirim'" if pending_only else ""
        rows = self._conn.execute(
            f"SELECT * FROM practice_session{where} ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        out = []
        for row in rows:
            value = dict(row)
            try:
                payload = json.loads(value.get("result_json") or "{}")
            except (TypeError, ValueError):
                payload = {}
            value.update(payload)
            value["sync"] = row["sync"]
            out.append(value)
        return out

    @locked
    def practice(self, sid: str) -> Optional[dict]:
        rows = self.practice_sessions(1000)
        return next((row for row in rows if row.get("id") == sid), None)

    @locked
    def tandai_practice_terkirim(self, sid: str, remote_id: Optional[str] = None,
                                *, revision: Optional[int] = None) -> bool:
        cur = self._conn.execute(
            "UPDATE practice_session SET sync='terkirim' WHERE id=?" +
            (" AND revision=?" if revision is not None else ""),
            (sid, revision) if revision is not None else (sid,))
        self._conn.commit()
        return cur.rowcount > 0

    @locked
    def set_practice_visibility(self, sid: str, visibility: str) -> bool:
        if visibility not in ("private", "public"):
            raise ValueError("visibility harus private atau public")
        row = self._conn.execute("SELECT result_json FROM practice_session WHERE id=?", (sid,)).fetchone()
        if not row:
            return False
        try:
            payload = json.loads(row["result_json"] or "{}")
        except (TypeError, ValueError):
            payload = {}
        payload["visibility"] = visibility
        cur = self._conn.execute("UPDATE practice_session SET visibility=?, sync='belum', result_json=? WHERE id=?",
                                 (visibility, json.dumps(payload, ensure_ascii=False), sid))
        self._conn.commit()
        return cur.rowcount > 0

    # -- race ------------------------------------------------------------
    @locked
    def simpan_race(self, race: dict) -> str:
        """Simpan sesi selesai beserta pembalap dan lap. Idempoten per id."""
        c = self._conn
        rid = race["id"]
        existing = c.execute("SELECT club_id FROM race WHERE id=?", (rid,)).fetchone()
        club_id = existing["club_id"] if existing else self.club().get("club_id")
        try:
            club_id = str(uuid.UUID(club_id)) if club_id else None
        except (ValueError, TypeError, AttributeError):
            club_id = None
        c.execute(
            "INSERT OR REPLACE INTO race (id, name, track_id, track_name, track_width,"
            " cfg, started_at, ended_at, status, sync, remote_id)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (rid, race["name"], race.get("track_id"), race.get("track_name"),
             race.get("track_width"), json.dumps(race.get("cfg") or {}),
             race["started_at"], race.get("ended_at"),
             race.get("status", "saved"), race.get("sync", "belum"),
             race.get("remote_id")))
        c.execute("DELETE FROM race_driver WHERE race_id = ?", (rid,))
        c.execute("DELETE FROM race_lap WHERE race_id = ?", (rid,))
        for d in race.get("drivers") or []:
            c.execute(
                "INSERT INTO race_driver (race_id, pos, uid, name, nick, kelas,"
                " transponder, luar, best_ms, dur_us, kmh)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (rid, d.get("pos"), d.get("uid"), d.get("name"), d.get("nick"),
                 d.get("kelas"), d.get("transponder"), 1 if d.get("luar") else 0,
                 d.get("best_ms"), d.get("dur_us"), d.get("kmh")))
            for seq, lap in enumerate(d.get("laps") or [], 1):
                c.execute(
                    "INSERT INTO race_lap (race_id, transponder, seq, lap_ms, at,"
                    " rssi, dur_us, skip) VALUES (?,?,?,?,?,?,?,0)",
                    (rid, d.get("transponder"), seq, int(lap),
                     race.get("ended_at") or race["started_at"], None, None))
        c.execute("UPDATE race SET result_json=?, club_id=? WHERE id=?", (json.dumps(race, ensure_ascii=False), club_id, rid))
        c.commit()
        return rid

    @locked
    def races(self, track_id: Optional[str] = None, limit: int = 200) -> list[dict]:
        q = ("SELECT r.*, (SELECT COUNT(*) FROM race_driver d WHERE d.race_id = r.id)"
             " AS n_driver, (SELECT COUNT(*) FROM race_lap l WHERE l.race_id = r.id)"
             " AS n_lap, (SELECT MIN(best_ms) FROM race_driver d WHERE d.race_id = r.id)"
             " AS best FROM race r")
        args: list = []
        if track_id:
            q += " WHERE r.track_id = ?"
            args.append(track_id)
        q += " ORDER BY r.started_at DESC LIMIT ?"
        args.append(limit)
        out = []
        for row in self._conn.execute(q, args).fetchall():
            d = dict(row)
            # cfg disimpan sebagai JSON; UI butuh objek, bukan string
            try:
                d["cfg"] = json.loads(d.get("cfg") or "{}")
            except ValueError:
                d["cfg"] = {}
            saved = json.loads(d.get("result_json") or "null")
            d.pop("result_json", None)
            if saved:
                d["elapsed_ms"] = max(0, (saved.get("end_decoder_ts") or 0) - (saved.get("start_decoder_ts") or 0))
            out.append(d)
        return out

    @locked
    def race(self, rid: str) -> Optional[dict]:
        r = self._conn.execute("SELECT * FROM race WHERE id = ?", (rid,)).fetchone()
        if not r:
            return None
        d = dict(r)
        if d.get("result_json"):
            saved = json.loads(d["result_json"])
            saved.update({k: d[k] for k in ("club_id", "sync", "remote_id")})
            return saved
        d["cfg"] = json.loads(d.get("cfg") or "{}")
        d["drivers"] = []
        for row in self._conn.execute(
                "SELECT * FROM race_driver WHERE race_id = ? ORDER BY pos", (rid,)):
            dd = dict(row)
            dd["laps"] = [x[0] for x in self._conn.execute(
                "SELECT lap_ms FROM race_lap WHERE race_id = ? AND transponder = ?"
                " ORDER BY seq", (rid, dd["transponder"]))]
            dd["kmh"] = None
            d["drivers"].append(dd)
        from .race_results import result_rows
        d["drivers"] = result_rows(d["drivers"], d["cfg"])
        return d

    @locked
    def import_remote_races(self, rows: list[dict], club_id: str) -> int:
        """Merge shared races from Supabase without replacing local pending work."""
        imported = 0
        for remote in rows or []:
            rid = str(remote.get("source_race_id") or remote.get("id") or "").strip()
            if not rid:
                continue
            old = self._conn.execute("SELECT sync, remote_id FROM race WHERE id=?", (rid,)).fetchone()
            if old and old["sync"] != "terkirim":
                # A local/offline result always wins until its upload is acknowledged.
                continue
            session = remote.get("session_data") or {}
            results = remote.get("results") or []
            drivers = []
            for i, row in enumerate(results, 1):
                laps = list(row.get("laps_ms") or row.get("laps") or [])
                drivers.append({
                    "uid": row.get("uid") or str(uuid.uuid4()),
                    "no": row.get("no") or i, "name": row.get("name") or row.get("driver") or "",
                    "nick": row.get("nickname") or "", "kelas": row.get("class") or row.get("kelas") or "",
                    "account_id": row.get("user_id") or row.get("driver_id"),
                    "member_id": row.get("user_id") or row.get("driver_id"),
                    "transponder": row.get("transponder") if row.get("transponder") is not None else -i, "laps": laps,
                    "passings": row.get("passings") or [], "pos": row.get("position"),
                    "best_ms": row.get("best_ms"), "top3_ms": row.get("top3_ms"),
                    "consecutive_ms": row.get("consecutive_ms"), "p80_ms": row.get("p80_ms"),
                    "consistency": row.get("consistency"), "top_speed_kmh": row.get("top_speed_kmh"),
                    "average_speed_kmh": row.get("average_speed_kmh"),
                })
            raced_at = remote.get("raced_at")
            try:
                started = datetime.datetime.fromisoformat(str(raced_at).replace("Z", "+00:00")).timestamp()
            except (TypeError, ValueError, OverflowError):
                started = time.time()
            data = {
                "id": rid, "club_id": club_id, "remote_id": remote.get("id"),
                "name": remote.get("name") or "Race", "track_id": session.get("track_id"),
                "track_name": remote.get("track_name") or "", "started_at": started,
                "ended_at": session.get("ended_at") or started, "status": "saved", "sync": "terkirim",
                "cfg": session.get("configuration") or {}, "loop_width_m": session.get("loop_width_m"),
                "start_decoder_ts": session.get("start_decoder_ts"), "end_decoder_ts": session.get("end_decoder_ts"),
                "finish_reason": session.get("finish_reason"), "timing_interrupted": session.get("timing_interrupted"),
                "drivers": drivers, "log": [],
                "source": session.get("source"),
            }
            self.simpan_race(data)
            self._conn.execute("UPDATE race SET sync='terkirim', remote_id=?, club_id=? WHERE id=?",
                               (remote.get("id"), club_id, rid))
            imported += 1
        self._conn.commit()
        return imported

    @locked
    def remove_missing_remote_races(self, remote_ids: set[str], club_id: str) -> int:
        """Reflect deletions made by another logged-in application."""
        rows = self._conn.execute(
            "SELECT id, remote_id FROM race WHERE club_id=? AND sync='terkirim' AND remote_id IS NOT NULL",
            (club_id,)).fetchall()
        removed = 0
        for row in rows:
            if row["remote_id"] not in remote_ids:
                self._conn.execute("DELETE FROM race WHERE id=?", (row["id"],))
                removed += 1
        if removed:
            self._conn.commit()
        return removed

    @locked
    def tandai_terkirim(self, rid: str, remote_id: Optional[str] = None) -> bool:
        cur = self._conn.execute(
            "UPDATE race SET sync = 'terkirim', remote_id = COALESCE(?, remote_id)"
            " WHERE id = ?", (remote_id, rid))
        self._conn.commit()
        return cur.rowcount > 0

    @locked
    def hapus_race(self, rid: str) -> bool:
        row = self._conn.execute("SELECT club_id, remote_id, track_id FROM race WHERE id=?", (rid,)).fetchone()
        if not row:
            return False
        with self._conn:
            if row["club_id"]:
                self._conn.execute("INSERT OR IGNORE INTO race_deletion(id,club_id,remote_id,deleted_at) VALUES (?,?,?,?)",
                                   (rid, row["club_id"], row["remote_id"], time.time()))
            self._conn.execute("DELETE FROM race WHERE id=?", (rid,))
        # Recompute the display record from remaining valid race snapshots.
        candidates = []
        for item in self._conn.execute("SELECT result_json FROM race WHERE track_id=?", (row["track_id"],)):
            saved = json.loads(item[0] or "{}")
            if not saved.get("timing_interrupted"):
                candidates.extend(d for d in saved.get("drivers", []) if d.get("best_ms"))
        winner = min(candidates, key=lambda d: d["best_ms"], default=None)
        self._conn.execute("UPDATE track SET best_ms=?,holder=?,synced=0,mutation_id=? WHERE id=?",
                           (winner["best_ms"] if winner else None,
                            (winner.get("nick") or winner.get("name")) if winner else None,
                            str(uuid.uuid4()), row["track_id"]))
        self._conn.commit()
        return True

    @locked
    def pending_race_deletions(self, club_id: str) -> list[dict]:
        return [dict(row) for row in self._conn.execute(
            "SELECT * FROM race_deletion WHERE club_id=? ORDER BY deleted_at", (club_id,))]

    @locked
    def acknowledge_race_deletion(self, rid: str) -> None:
        self._conn.execute("DELETE FROM race_deletion WHERE id=?", (rid,))
        self._conn.commit()

    @locked
    def ringkasan(self) -> dict:
        c = self._conn
        return {
            "races": c.execute("SELECT COUNT(*) FROM race").fetchone()[0],
            "belum_terkirim": c.execute(
                "SELECT COUNT(*) FROM race WHERE sync != 'terkirim'").fetchone()[0],
            "total_lap": c.execute("SELECT COUNT(*) FROM race_lap").fetchone()[0],
            "tracks": c.execute("SELECT COUNT(*) FROM track WHERE deleted=0").fetchone()[0],
            "members": c.execute("SELECT COUNT(*) FROM member").fetchone()[0],
            "track_belum_sinkron": c.execute(
                "SELECT COUNT(*) FROM track WHERE synced = 0").fetchone()[0],
        }

    @locked
    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass


# The decoder callback and ThreadingHTTPServer share this connection.
from functools import wraps as _wraps

def _locked(method):
    @_wraps(method)
    def wrapped(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return wrapped

for _name, _method in list(vars(ClubStore).items()):
    if callable(_method) and not _name.startswith("_"):
        setattr(ClubStore, _name, _locked(_method))
