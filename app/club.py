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
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Optional

from .config import default_data_dir

SCHEMA_VERSION = 2

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
    width_m     REAL,                 -- keliling loop, untuk km/h
    note        TEXT,
    best_ms     INTEGER,
    holder      TEXT,
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
    updated_at  REAL
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

CREATE INDEX IF NOT EXISTS idx_lap_race ON race_lap (race_id, transponder);
CREATE INDEX IF NOT EXISTS idx_rd_race  ON race_driver (race_id);
"""

# Fixture data contoh untuk pengujian; tidak dimasukkan ke database pengguna.
CONTOH_TRACK = [
    ("t-medan-a", "Sirkuit Medan RC · Layout A", "Jl. Gatot Subroto, Medan", 42.0,
     "4 tikungan, aspal", 9402, "Budi", 1),
    ("t-medan-b", "Sirkuit Medan RC · Layout B", "Jl. Gatot Subroto, Medan", 58.0,
     "6 tikungan, aspal", 12118, "Andi", 1),
    ("t-deli-oval", "Lapangan Deli Serdang · Oval", "Lubuk Pakam, Deli Serdang", 35.0,
     "oval, beton", 7880, "Citra", 0),
]

CONTOH_MEMBER = [
    ("u-budi", "Budi Santoso", "Budi", "Mod", "Xray X4", "leader", "active", [1615544]),
    ("u-andi", "Andi Pratama", "Andi", "Stock", "Tamiya", "co_leader", "active", [1712003, 1712004]),
    ("u-citra", "Citra Dewi", "Citra", "Mod", "Yokomo", "member", "active", [1810044]),
    ("u-rudi", "Rudi Hartono", "Rudi", "Stock", "Tamiya", "officer", "active", [1912007]),
    ("u-sari", "Sari Wulandari", "Sari", "Mod", "—", "member", "pending", []),
]

CONTOH_PRESET = [
    ("p-sprint", "Sprint 3 menit", "time", 180, "lapsTime", "grid", 5, 1),
    ("p-final", "Final 5 menit · 10 lap", "laps", 10, "best", "grid", 5, 1),
    ("p-endurance", "Endurance 10 menit", "time", 600, "top3", "staggered", 10, 1),
]


class ClubStore:
    """Akses SQLite untuk club, track, member, preset, dan hasil balapan."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else default_data_dir() / "yurlaps_club.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self.migrate()
        self._seed()

    # -- migrasi ---------------------------------------------------------
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
            if v < 2:
                # Race lama sengaja tidak dihubungkan otomatis: klub asalnya
                # tidak dapat dibuktikan dari database versi sebelumnya.
                conn.execute("ALTER TABLE race ADD COLUMN club_id TEXT")
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.isolation_level = lama
        return dibuat

    def _seed(self) -> None:
        """Siapkan preset balapan tanpa membuat club, track, atau member palsu."""
        c = self._conn
        now = time.time()
        if not c.execute("SELECT 1 FROM club WHERE id = 1").fetchone():
            c.execute("INSERT INTO club (id, club_id, name, role, synced_at) "
                      "VALUES (1, NULL, '', NULL, NULL)")
        if not c.execute("SELECT 1 FROM preset LIMIT 1").fetchone():
            for pid, name, target, value, rank, start, cd, sound in CONTOH_PRESET:
                c.execute("INSERT INTO preset (id, name, target, value, rank, start_mode,"
                          " cd, sound, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                          (pid, name, target, value, rank, start, cd, sound, now))
        c.commit()

    # -- club ------------------------------------------------------------
    def club(self) -> dict:
        r = self._conn.execute("SELECT * FROM club WHERE id = 1").fetchone()
        return dict(r) if r else {}

    def set_club(self, **kw) -> None:
        kolom = [k for k in ("club_id", "name", "role", "synced_at") if k in kw]
        if not kolom:
            return
        sets = ", ".join(f"{k} = ?" for k in kolom)
        self._conn.execute(f"UPDATE club SET {sets} WHERE id = 1",
                           [kw[k] for k in kolom])
        self._conn.commit()

    # -- track -----------------------------------------------------------
    def tracks(self) -> list[dict]:
        """Daftar track + jumlah sesi per track.

        ``n_race`` dihitung di sini (bukan di UI) supaya angka "0 sesi di track
        ini" tidak pernah salah walaupun daftar riwayat belum dimuat.
        """
        rows = self._conn.execute(
            "SELECT t.*, (SELECT COUNT(*) FROM race r WHERE r.track_id = t.id)"
            " AS n_race FROM track t"
            " ORDER BY t.synced ASC, t.name COLLATE NOCASE").fetchall()
        return [dict(r) for r in rows]

    def track(self, track_id: str) -> Optional[dict]:
        r = self._conn.execute(
            "SELECT t.*, (SELECT COUNT(*) FROM race r WHERE r.track_id = t.id)"
            " AS n_race FROM track t WHERE t.id = ?", (track_id,)).fetchone()
        return dict(r) if r else None

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

        if tid and self.track(tid):
            self._conn.execute(
                "UPDATE track SET name=?, addr=?, width_m=?, note=?, synced=0,"
                " updated_at=? WHERE id=?",
                (nama, data.get("addr") or "", lebar, data.get("note") or "", now, tid))
        else:
            tid = tid or f"t{int(now*1000)}"
            self._conn.execute(
                "INSERT INTO track (id, name, addr, width_m, note, best_ms, holder,"
                " synced, updated_at) VALUES (?,?,?,?,?,NULL,NULL,0,?)",
                (tid, nama, data.get("addr") or "", lebar, data.get("note") or "", now))
        self._conn.commit()
        return self.track(tid) or {}

    def delete_track(self, track_id: str) -> bool:
        if self._conn.execute("SELECT COUNT(*) FROM track").fetchone()[0] <= 1:
            raise ValueError("Minimal satu track harus ada")
        cur = self._conn.execute("DELETE FROM track WHERE id = ?", (track_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def mark_tracks_synced(self) -> int:
        cur = self._conn.execute("UPDATE track SET synced = 1 WHERE synced = 0")
        self._conn.commit()
        return cur.rowcount

    def perbarui_rekor(self, track_id: str, best_ms: Optional[int], holder: str) -> None:
        if best_ms is None:
            return
        t = self.track(track_id)
        if not t:
            return
        if not t.get("best_ms") or best_ms < t["best_ms"]:
            self._conn.execute("UPDATE track SET best_ms = ?, holder = ? WHERE id = ?",
                               (best_ms, holder, track_id))
            self._conn.commit()

    # -- member ----------------------------------------------------------
    def members(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM member ORDER BY status ASC, name COLLATE NOCASE").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["transponder"] = json.loads(d.get("transponder") or "[]")
            out.append(d)
        return out

    # -- preset ----------------------------------------------------------
    def presets(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM preset ORDER BY rowid").fetchall()
        return [dict(r) for r in rows]

    def preset(self, pid: str) -> Optional[dict]:
        r = self._conn.execute("SELECT * FROM preset WHERE id = ?", (pid,)).fetchone()
        return dict(r) if r else None

    def save_preset(self, data: dict) -> dict:
        now = time.time()
        pid = (data.get("id") or "").strip()
        nama = (data.get("name") or "").strip()
        if not nama:
            raise ValueError("Nama preset wajib diisi")
        target = data.get("target") if data.get("target") in ("time", "laps") else "time"
        rank = data.get("rank") if data.get("rank") in (
            "lapsTime", "best", "top3", "consecutive") else "lapsTime"
        start = data.get("start_mode") if data.get("start_mode") in (
            "grid", "staggered") else "grid"
        nilai = int(data.get("value") or 180)
        cd = int(data.get("cd") or 5)
        sound = 1 if data.get("sound", 1) else 0

        if pid and self.preset(pid):
            self._conn.execute(
                "UPDATE preset SET name=?, target=?, value=?, rank=?, start_mode=?,"
                " cd=?, sound=?, updated_at=? WHERE id=?",
                (nama, target, nilai, rank, start, cd, sound, now, pid))
        else:
            pid = pid or f"p{int(now*1000)}"
            self._conn.execute(
                "INSERT INTO preset (id, name, target, value, rank, start_mode, cd,"
                " sound, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (pid, nama, target, nilai, rank, start, cd, sound, now))
        self._conn.commit()
        return self.preset(pid) or {}

    def delete_preset(self, pid: str) -> bool:
        if self._conn.execute("SELECT COUNT(*) FROM preset").fetchone()[0] <= 1:
            raise ValueError("Minimal satu preset harus ada")
        cur = self._conn.execute("DELETE FROM preset WHERE id = ?", (pid,))
        self._conn.commit()
        return cur.rowcount > 0

    # -- race ------------------------------------------------------------
    def simpan_race(self, race: dict) -> str:
        """Simpan sesi selesai beserta pembalap dan lap. Idempoten per id."""
        c = self._conn
        rid = race["id"]
        existing = c.execute("SELECT club_id FROM race WHERE id = ?", (rid,)).fetchone()
        club_id = existing["club_id"] if existing else self.club().get("club_id")
        if club_id:
            try:
                club_id = str(uuid.UUID(club_id))
            except (ValueError, TypeError, AttributeError):
                club_id = None
        c.execute(
            "INSERT OR REPLACE INTO race (id, name, track_id, track_name, track_width,"
            " cfg, started_at, ended_at, status, sync, remote_id, club_id)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, race["name"], race.get("track_id"), race.get("track_name"),
             race.get("track_width"), json.dumps(race.get("cfg") or {}),
             race["started_at"], race.get("ended_at"),
             race.get("status", "saved"), race.get("sync", "belum"),
             race.get("remote_id"), club_id))
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
        c.commit()
        return rid

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
            out.append(d)
        return out

    def race(self, rid: str) -> Optional[dict]:
        r = self._conn.execute("SELECT * FROM race WHERE id = ?", (rid,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["cfg"] = json.loads(d.get("cfg") or "{}")
        d["drivers"] = []
        for row in self._conn.execute(
                "SELECT * FROM race_driver WHERE race_id = ? ORDER BY pos", (rid,)):
            dd = dict(row)
            dd["laps"] = [x[0] for x in self._conn.execute(
                "SELECT lap_ms FROM race_lap WHERE race_id = ? AND transponder = ?"
                " ORDER BY seq", (rid, dd["transponder"]))]
            d["drivers"].append(dd)
        return d

    def tandai_terkirim(self, rid: str, remote_id: Optional[str] = None) -> bool:
        cur = self._conn.execute(
            "UPDATE race SET sync = 'terkirim', remote_id = COALESCE(?, remote_id)"
            " WHERE id = ?", (remote_id, rid))
        self._conn.commit()
        return cur.rowcount > 0

    def hapus_race(self, rid: str) -> bool:
        cur = self._conn.execute("DELETE FROM race WHERE id = ?", (rid,))
        self._conn.commit()
        return cur.rowcount > 0

    def ringkasan(self) -> dict:
        c = self._conn
        return {
            "races": c.execute("SELECT COUNT(*) FROM race").fetchone()[0],
            "belum_terkirim": c.execute(
                "SELECT COUNT(*) FROM race WHERE sync != 'terkirim'").fetchone()[0],
            "total_lap": c.execute("SELECT COUNT(*) FROM race_lap").fetchone()[0],
            "tracks": c.execute("SELECT COUNT(*) FROM track").fetchone()[0],
            "members": c.execute("SELECT COUNT(*) FROM member").fetchone()[0],
            "track_belum_sinkron": c.execute(
                "SELECT COUNT(*) FROM track WHERE synced = 0").fetchone()[0],
        }

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
