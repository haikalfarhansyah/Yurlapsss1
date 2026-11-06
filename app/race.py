"""Mesin Quick Race untuk YurLaps mode offline.

Bertanggung jawab atas satu sesi balapan: peserta, hitung mundur, pencatatan
lintasan, pengurutan klasemen, dan penyimpanan ke riwayat.

Timestamp decoder OpenStint adalah satu-satunya acuan waktu penilaian.
Jam komputer hanya dipakai untuk tanggal arsip dan diagnostik koneksi.

Tidak ada jaringan di sini.
"""
from __future__ import annotations

import statistics
import threading
import time
import uuid
from typing import Optional

from .club import ClubStore


def kmh(loop_width_m: Optional[float], passing_us: Optional[int]) -> Optional[float]:
    """Jarak antarkabel loop / durasi passing mikrodetik, dalam km/h."""
    import math
    if not loop_width_m or not passing_us or loop_width_m <= 0 or passing_us <= 0:
        return None
    value = loop_width_m * 3_600_000 / passing_us
    return round(value, 2) if math.isfinite(value) else None


URUTAN = {
    "lapsTime": "Lap -> total waktu",
    "best": "Best lap",
    "top3": "Rata-rata 3 lap tercepat",
    "consecutive": "3 lap berurutan tercepat",
}


class RaceEngine:
    """Satu sesi Quick Race dalam memori. Disimpan ke SQLite saat Finish."""

    def __init__(self, store: ClubStore):
        self.store = store
        self.lock = threading.RLock()
        self._track_snapshot = None
        self.status = "idle"          # idle | countdown | live | done
        self.mulai_mono = 0.0
        self.mulai_wall = 0.0
        self.akhir_wall: Optional[float] = None
        self.cfg = dict(store.preset(store.presets()[0]["id"]) or {})
        self.cfg.pop("updated_at", None)
        self.cfg["nama"] = self.cfg.pop("name", "Quick Race")
        self.track_id = (store.tracks() or [{}])[0].get("id")
        self.peserta: list[dict] = []
        self.log: list[dict] = []
        self.pilihan: Optional[str] = None
        self.alasan_selesai = ""
        self.decoder_ts = None
        self.decoder_received_mono = 0.0
        self.start_decoder_ts = None
        self.end_decoder_ts = None
        self.race_track = None

    def observe_clock(self, timestamp):
        if timestamp is None:
            return
        with self.lock:
            self.decoder_ts = int(timestamp)
            self.decoder_received_mono = time.monotonic()

    def clock_reset(self):
        with self.lock:
            if self.status in ("live", "countdown"):
                self._selesai("Decoder restart: timestamp direset. Mulai sesi baru.")
            self.decoder_ts = None
            self.decoder_received_mono = 0

    def _track(self):
        if self.status in ("countdown", "live", "done") and self._track_snapshot is not None:
            return self._track_snapshot
        return self.store.track(self.track_id)

    # -- konfigurasi -----------------------------------------------------
    def set_cfg(self, data: dict) -> dict:
        with self.lock:
            for k in ("nama", "name"):
                if k in data:
                    self.cfg["nama"] = data[k]
            if data.get("target") in ("time", "laps"):
                self.cfg["target"] = data["target"]
            if data.get("value") is not None:
                self.cfg["value"] = int(data["value"])
            if data.get("rank") in URUTAN:
                self.cfg["rank"] = data["rank"]
            if data.get("start_mode") in ("grid", "staggered"):
                self.cfg["start_mode"] = data["start_mode"]
            if data.get("cd") is not None:
                self.cfg["cd"] = int(data["cd"])
            if data.get("sound") is not None:
                self.cfg["sound"] = 1 if data["sound"] else 0
            return dict(self.cfg)

    def set_track(self, track_id: str) -> None:
        with self.lock:
            if self.status in ("live", "countdown"):
                raise ValueError("Track tidak bisa diganti saat sesi berjalan")
            if not self.store.track(track_id):
                raise ValueError("Track tidak ditemukan")
            self.track_id = track_id

    def set_peserta(self, daftar: list[dict]) -> list[dict]:
        """Ganti daftar peserta. Ditolak kalau sesi sedang berjalan."""
        with self.lock:
            if self.status in ("live", "countdown"):
                raise ValueError("Peserta tidak bisa diubah saat sesi berjalan")
            member = {m["id"]: m for m in self.store.members()}
            out = []
            for i, p in enumerate(daftar, 1):
                mid = p.get("member_id") or p.get("id") or ""
                m = member.get(mid, {})
                tp = p.get("transponder")
                if tp in (None, ""):
                    tps = m.get("transponder") or []
                    tp = tps[0] if tps else None
                try:
                    tp = int(tp) if tp not in (None, "") else None
                except (TypeError, ValueError):
                    tp = None
                out.append({
                    "uid": p.get("uid") or f"d{i}-{uuid.uuid4().hex[:6]}",
                    "no": int(p.get("no") or i),
                    "member_id": mid,
                    "name": p.get("name") or m.get("name") or f"Pembalap {i}",
                    "nick": p.get("nick") or m.get("nick") or "",
                    "kelas": p.get("kelas") or m.get("kelas") or "",
                    "transponder": tp,
                    # "bukan member" hanya untuk yang memang tidak ada di club.
                    # Member tanpa transponder tetap member (hanya belum bisa
                    # mencatat lap sampai transpondernya dipetakan).
                    "luar": not m,
                    "laps": [],
                    "dur_us": None,
                    "terakhir_mono": 0.0,
                })
            out.sort(key=lambda x: x["no"])
            for i, p in enumerate(out, 1):
                p["no"] = i
            ids = [p["transponder"] for p in out if p["transponder"] is not None]
            if len(ids) != len(set(ids)):
                raise ValueError("Satu transponder hanya boleh dipakai satu peserta")
            self.peserta = out
            if out and not any(p["uid"] == self.pilihan for p in out):
                self.pilihan = out[0]["uid"]
            self.log = []
            return out

    # -- sesi ------------------------------------------------------------
    def mulai(self) -> dict:
        with self.lock:
            if not self.peserta:
                raise ValueError("Pilih peserta dulu")
            if self.status == "live":
                raise ValueError("Sesi sedang berjalan")
            self._track_snapshot = dict(self.store.track(self.track_id) or {})
            for p in self.peserta:
                p["laps"] = []
                p["dur_us"] = None
                p["terakhir_mono"] = 0.0
                p["last_decoder_ts"] = None
                p["first_decoder_ts"] = None
                p["passings"] = []
                p["finish_decoder_ts"] = None
            self.log = []
            self.akhir_wall = None
            self.alasan_selesai = ""
            self.status = "countdown"
            return self.snapshot()

    def go(self) -> dict:
        """Dipanggil setelah hitung mundur selesai."""
        with self.lock:
            if self.status != "countdown":
                raise ValueError("Sesi belum siap dimulai")
            if self.decoder_ts is None or time.monotonic() - self.decoder_received_mono > 3:
                raise ValueError("Belum ada jam decoder aktif. Tunggu status OpenStint sebelum GO.")
            self.start_decoder_ts = self.decoder_ts
            self.end_decoder_ts = None
            self.race_track = dict(self.store.track(self.track_id) or {})
            self.status = "live"
            self.mulai_mono = time.monotonic()
            self.mulai_wall = time.time()
            return self.snapshot()

    def reset(self) -> dict:
        with self.lock:
            self.status = "idle"
            self.mulai_mono = 0.0
            self.mulai_wall = 0.0
            self.akhir_wall = None
            self.log = []
            self.alasan_selesai = ""
            for p in self.peserta:
                p["laps"] = []
                p["dur_us"] = None
                p["terakhir_mono"] = 0.0
                p["last_decoder_ts"] = None
                p["first_decoder_ts"] = None
                p["passings"] = []
                p["finish_decoder_ts"] = None
            return self.snapshot()

    def elapsed_ms(self) -> int:
        end = self.end_decoder_ts if self.status == "done" else self.decoder_ts
        if self.start_decoder_ts is None or end is None or self.status not in ("live", "done"):
            return 0
        return max(0, end - self.start_decoder_ts)

    def sisa_ms(self) -> Optional[int]:
        if self.cfg.get("target") != "time":
            return None
        return max(0, int(self.cfg.get("value", 180)) * 1000 - self.elapsed_ms())

    # -- lintasan --------------------------------------------------------
    def catat(self, transponder: int, *, decoder_ts=None, rssi=None,
              hit_count=None, dur_us=None, **unused) -> dict:
        """Raw passing selalu terlihat, termasuk transponder di luar peserta.

        Penilaian hanya memakai timestamp P. Pesan terlambat/duplikat tidak
        menggeser acuan lap. Jam penerimaan PC tidak dipakai sebagai cadangan.
        """
        with self.lock:
            p = next((x for x in self.peserta if x["transponder"] == transponder), None)
            member = self.store.member_for_transponder(transponder)
            entry = {"transponder": transponder, "decoder_timecode": decoder_ts,
                     "t": decoder_ts, "jam": str(decoder_ts) if decoder_ts is not None else "—",
                     "nama": (p["nick"] or p["name"]) if p else
                             ((member.get("nick") or member["name"]) if member else f"TP {transponder}"),
                     "rssi": rssi, "hit_count": hit_count, "dur_us": dur_us,
                     "lap": None, "skip": True, "kmh": None}
            self.log.insert(0, entry)
            del self.log[60:]
            if decoder_ts is None or not isinstance(decoder_ts, int) or decoder_ts < 0:
                return {"diterima": False, "alasan": "timestamp decoder tidak sah"}
            if self.decoder_ts is None or decoder_ts > self.decoder_ts:
                self.observe_clock(decoder_ts)
            if not p or self.status != "live":
                return {"diterima": False, "alasan": "bukan peserta" if not p else "sesi belum berjalan", "entri": entry}
            if decoder_ts < self.start_decoder_ts:
                return {"diterima": False, "alasan": "lintasan sebelum GO"}
            if p.get("finish_decoder_ts") is not None:
                return {"diterima": False, "alasan": "peserta sudah finish"}
            previous = p.get("last_decoder_ts")
            if previous is not None and decoder_ts <= previous:
                return {"diterima": False, "alasan": "timestamp duplikat atau terlambat"}
            if self.cfg.get("target") == "time" and decoder_ts > self.start_decoder_ts + int(self.cfg.get("value", 180))*1000:
                return {"diterima": False, "alasan": "melewati batas waktu decoder"}
            first = previous is None
            baseline = self.start_decoder_ts if first else previous
            lap_ms = decoder_ts - baseline
            if not first and lap_ms < int(self.cfg.get("min_lap_ms", 1500)):
                return {"diterima": False, "alasan": "lintasan ganda / di bawah minimum lap"}
            if first and self.cfg.get("start_mode") != "staggered" and lap_ms <= 0:
                return {"diterima": False, "alasan": "waktu lap nol"}
            if first:
                p["first_decoder_ts"] = decoder_ts
            p["last_decoder_ts"] = decoder_ts
            p["dur_us"] = dur_us
            is_origin = first and self.cfg.get("start_mode") == "staggered"
            if not is_origin and lap_ms <= 0:
                return {"diterima": False, "alasan": "waktu lap nol"}
            if not is_origin:
                p["laps"].append(lap_ms)
            speed = kmh((self.race_track or {}).get("loop_width_m"), dur_us)
            raw = {"decoder_timecode": decoder_ts, "rssi": rssi,
                   "hit_count": hit_count, "pass_duration_us": dur_us,
                   "speed_kmh": speed, "lap_ms": None if is_origin else lap_ms,
                   "origin": is_origin}
            p.setdefault("passings", []).append(raw)
            entry.update(lap=len(p["laps"]), skip=False, kmh=speed,
                         origin=is_origin)
            if self.cfg.get("target") == "laps" and len(p["laps"]) >= int(self.cfg.get("value", 10)):
                p["finish_decoder_ts"] = decoder_ts
            done = self._cek_selesai()
            return {"diterima": True, "lap": len(p["laps"]), "lap_ms": raw["lap_ms"],
                    "entri": entry, "selesai": done}

    def _cek_selesai(self) -> bool:
        """Target lap tercapai hanya dihitung dari peserta yang punya transponder.

        Peserta tanpa transponder (mis. member baru yang belum dipetakan) tidak
        akan pernah mencatat lap; kalau ikut dihitung, sesi tidak akan pernah
        selesai otomatis.
        """
        if self.cfg.get("target") == "laps":
            target = int(self.cfg.get("value", 10))
            aktif = [p for p in self.peserta if p["transponder"] is not None]
            if aktif and all(len(p["laps"]) >= target for p in aktif):
                self._selesai("semua lap tercatat")
                return True
        return False

    def cek_waktu(self) -> bool:
        """Dipanggil berkala oleh API: hentikan sesi bila waktu habis."""
        with self.lock:
            if self.status == "live" and self.cfg.get("target") == "time":
                if self.elapsed_ms() >= int(self.cfg.get("value", 180)) * 1000 + 2000:
                    self._selesai("waktu habis")
                    return True
            return False

    def _selesai(self, alasan: str) -> None:
        self.end_decoder_ts = self.decoder_ts
        self.status = "done"
        self.akhir_wall = time.time()
        self.alasan_selesai = alasan

    def selesai(self, alasan: str = "dihentikan operator") -> dict:
        with self.lock:
            if self.status != "live":
                raise ValueError("Sesi tidak sedang berjalan")
            self._selesai(alasan)
            return self.snapshot()

    # -- klasemen --------------------------------------------------------
    def _skor(self, p: dict) -> list[float]:
        h = sorted(p["laps"])
        rank = self.cfg.get("rank", "lapsTime")
        if rank == "best":
            return [h[0] if h else float("inf")]
        if rank == "top3":
            return [statistics.mean(h[:3]) if len(h) >= 3 else float("inf")]
        if rank == "consecutive":
            tiga = [(p["laps"][i - 2] + p["laps"][i - 1] + p["laps"][i]) / 3
                    for i in range(2, len(p["laps"]))]
            return [min(tiga) if tiga else float("inf")]
        return [-len(p["laps"]),
                sum(p["laps"]) if p["laps"] else float("inf")]

    def klasemen(self) -> list[dict]:
        from .race_results import result_rows
        with self.lock:
            drivers = []
            for p in self.peserta:
                d = dict(p)
                d["laps"] = list(p["laps"])
                d["passings"] = list(p.get("passings", []))
                drivers.append(d)
            return result_rows(drivers, self.cfg)

    # -- simpan ----------------------------------------------------------
    def simpan(self, nama: Optional[str] = None) -> dict:
        with self.lock:
            if self.status not in ("done", "live"):
                raise ValueError("Belum ada sesi untuk disimpan")
            if self.status == "live":
                self._selesai("disimpan")
            t = self.race_track or self.store.track(self.track_id) or {}
            kal = self.klasemen()
            rid = str(uuid.uuid4())
            jumlah = sum(d["n_lap"] for d in kal)
            total = self.store.races()
            nama = (nama or "").strip() or \
                f"{self.cfg.get('nama', 'Quick Race')} #{len(total) + 1}"
            race = {
                "id": rid, "name": nama, "track_id": self.track_id,
                "track_name": t.get("name"), "track_width": t.get("width_m"),
                "cfg": dict(self.cfg), "started_at": self.mulai_wall or time.time(),
                "ended_at": self.akhir_wall or time.time(),
                "status": "saved", "sync": "belum",
                "start_decoder_ts": self.start_decoder_ts,
                "end_decoder_ts": self.end_decoder_ts,
                "loop_width_m": t.get("loop_width_m"),
                "finish_reason": self.alasan_selesai,
                "drivers": [dict(d, result_status=("DNS" if not d["n_lap"] else
                    "DNF" if self.cfg.get("target") == "laps" and d["n_lap"] < int(self.cfg.get("value", 10)) else "FIN")) for d in kal],
            }
            self.store.simpan_race(race)
            juara = min((d for d in kal if d["best_ms"] is not None),
                        key=lambda d: d["best_ms"], default=None)
            if juara:
                self.store.perbarui_rekor(self.track_id, juara["best_ms"],
                                          juara["nick"] or juara["name"])
            self.status = "idle"
            return {"id": rid, "nama": nama, "jumlah_lap": jumlah,
                    "pembalap": len(kal)}

    def buang(self) -> None:
        with self.lock:
            self.status = "idle"
            self.mulai_mono = 0.0
            self.akhir_wall = None
            self.log = []
            for p in self.peserta:
                p["laps"] = []
                p["dur_us"] = None
                p["terakhir_mono"] = 0.0
                p["last_decoder_ts"] = None
                p["first_decoder_ts"] = None
                p["passings"] = []
                p["finish_decoder_ts"] = None

    # -- snapshot untuk UI ----------------------------------------------
    def snapshot(self) -> dict:
        with self.lock:
            return {
                "status": self.status,
                "cfg": dict(self.cfg),
                "track_id": self.track_id,
                "track": self.store.track(self.track_id),
                "peserta": [{"uid": p["uid"], "no": p["no"],
                             "member_id": p["member_id"], "name": p["name"],
                             "nick": p["nick"], "kelas": p["kelas"],
                             "transponder": p["transponder"], "luar": p["luar"],
                             "laps": list(p["laps"]), "dur_us": p["dur_us"]}
                            for p in self.peserta],
                "klasemen": self.klasemen() if self.peserta else [],
                "log": list(self.log[:30]),
                "elapsed_ms": self.elapsed_ms(),
                "sisa_ms": self.sisa_ms(),
                "pilihan": self.pilihan,
                "alasan_selesai": self.alasan_selesai,
                "start_decoder_ts": self.start_decoder_ts,
                "clock_stale": time.monotonic() - self.decoder_received_mono > 3,
            }
