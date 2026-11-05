"""Mesin Quick Race untuk YurLaps mode offline.

Bertanggung jawab atas satu sesi balapan: peserta, hitung mundur, pencatatan
lintasan, pengurutan klasemen, dan penyimpanan ke riwayat.

Semua hitungan waktu memakai ``time.monotonic()`` (bukan jam dinding) supaya
tidak terpengaruh perubahan waktu sistem di laptop sirkuit. Jam dinding hanya
dipakai untuk mencatat kapan sesi mulai/berakhir.

Tidak ada jaringan di sini.
"""
from __future__ import annotations

import statistics
import threading
import time
import uuid
from typing import Optional

from .club import ClubStore


def kmh(keliling_m: Optional[float], lap_ms: Optional[int]) -> Optional[int]:
    """Kecepatan rata-rata satu lap.

    ``keliling_m / (lap_ms / 1000) * 3.6`` = km/h.

    Contoh: 42 m / 9,412 s -> 16 km/h.

    Catatan penting: JANGAN memakai ``pass_duration`` (lama mobil di bawah
    antena, sekitar 0,1 s) sebagai pembagi -- hasilnya ~1.500 km/h, mustahil.
    Nilai itu tetap disimpan sebagai ``dur_us`` untuk menilai kualitas sinyal,
    tetapi tidak dipakai menghitung kecepatan lap.
    """
    if not keliling_m or not lap_ms:
        return None
    return round((keliling_m / (lap_ms / 1000.0)) * 3.6)


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
            for p in self.peserta:
                p["laps"] = []
                p["dur_us"] = None
                p["terakhir_mono"] = 0.0
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
            return self.snapshot()

    def elapsed_ms(self) -> int:
        if self.status == "live":
            return int((time.monotonic() - self.mulai_mono) * 1000)
        if self.status == "done" and self.akhir_wall and self.mulai_wall:
            return int((self.akhir_wall - self.mulai_wall) * 1000)
        return 0

    def sisa_ms(self) -> Optional[int]:
        if self.cfg.get("target") != "time":
            return None
        return max(0, int(self.cfg.get("value", 180)) * 1000 - self.elapsed_ms())

    # -- lintasan --------------------------------------------------------
    def catat(self, transponder: int, *, rssi: float = -52.0,
              dur_us: Optional[int] = None, waktu_mono: Optional[float] = None,
              paksa_lap_ms: Optional[int] = None) -> dict:
        """Catat satu lintasan untuk transponder tertentu.

        ``paksa_lap_ms`` HANYA dipakai tombol "Lintasan (uji)" di UI: tanpa itu,
        klik beruntun menghasilkan lap 0,2 detik dan kecepatan ratusan km/h yang
        membingungkan saat menguji tampilan. Jalur decoder sungguhan tidak
        memakai parameter ini -- waktunya selalu dari selisih lintasan nyata.
        """
        with self.lock:
            now = waktu_mono if waktu_mono is not None else time.monotonic()
            p = next((x for x in self.peserta if x["transponder"] == transponder), None)
            if p is None:
                # transponder di luar daftar peserta: tetap dicatat sebagai log
                entri = {
                    "t": int((now - self.mulai_mono) * 1000) if self.mulai_mono else 0,
                    "jam": time.strftime("%H:%M:%S"), "transponder": transponder,
                    "nama": f"TP {transponder}", "rssi": rssi, "lap": None,
                    "skip": True, "dur_us": dur_us, "kmh": None,
                }
                self.log.insert(0, entri)
                del self.log[60:]
                return {"diterima": False, "alasan": "bukan peserta", "entri": entri}

            if self.status != "live":
                return {"diterima": False, "alasan": "sesi belum berjalan"}

            # lap pertama dihitung sejak GO, selanjutnya selisih antar lintasan
            acuan = p["terakhir_mono"] or self.mulai_mono
            lap_ms = int((now - acuan) * 1000)
            if paksa_lap_ms:
                lap_ms = int(paksa_lap_ms)
            if lap_ms <= 0:
                return {"diterima": False, "alasan": "waktu tidak sah"}
            p["terakhir_mono"] = now
            p["laps"].append(lap_ms)
            p["dur_us"] = dur_us

            t = self.store.track(self.track_id) or {}
            v = kmh(t.get("width_m"), lap_ms)
            entri = {
                "t": int((now - self.mulai_mono) * 1000),
                "jam": time.strftime("%H:%M:%S"),
                "transponder": transponder, "nama": p["nick"] or p["name"],
                "rssi": rssi, "lap": len(p["laps"]), "skip": False,
                "dur_us": dur_us, "kmh": v,
            }
            self.log.insert(0, entri)
            del self.log[60:]

            selesai_otomatis = self._cek_selesai()
            return {"diterima": True, "lap": len(p["laps"]), "lap_ms": lap_ms,
                    "kmh": v, "entri": entri, "selesai": selesai_otomatis}

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
                if self.elapsed_ms() >= int(self.cfg.get("value", 180)) * 1000:
                    self._selesai("waktu habis")
                    return True
            return False

    def _selesai(self, alasan: str) -> None:
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
        with self.lock:
            def kunci(p):
                return (self._skor(p), p["no"])
            urut = sorted(self.peserta, key=lambda p: (self._skor(p), p["no"]))
            t = self.store.track(self.track_id) or {}
            keliling = t.get("width_m")
            out = []
            for i, p in enumerate(urut, 1):
                best = min(p["laps"]) if p["laps"] else None
                rata = round(statistics.mean(p["laps"])) if p["laps"] else None
                sig = (round(statistics.pstdev(p["laps"]))
                       if len(p["laps"]) > 1 else None)
                out.append({
                    "pos": i, "uid": p["uid"], "no": p["no"], "name": p["name"],
                    "nick": p["nick"], "kelas": p["kelas"], "luar": p["luar"],
                    "transponder": p["transponder"], "laps": list(p["laps"]),
                    "n_lap": len(p["laps"]), "best_ms": best, "rata_ms": rata,
                    "sigma_ms": sig, "dur_us": p["dur_us"],
                    "kmh": kmh(keliling, best), "terakhir_ms": p["laps"][-1] if p["laps"] else None,
                })
            return out

    # -- simpan ----------------------------------------------------------
    def simpan(self, nama: Optional[str] = None) -> dict:
        with self.lock:
            if self.status not in ("done", "live"):
                raise ValueError("Belum ada sesi untuk disimpan")
            if self.status == "live":
                self._selesai("disimpan")
            t = self.store.track(self.track_id) or {}
            kal = self.klasemen()
            # UUID tidak bentrok antar-perangkat saat hasil diunggah ulang.
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
                "drivers": [{
                    "pos": d["pos"], "uid": d["uid"], "name": d["name"], "nick": d["nick"],
                    "kelas": d["kelas"], "transponder": d["transponder"],
                    "luar": d["luar"], "best_ms": d["best_ms"], "dur_us": d["dur_us"],
                    "kmh": d["kmh"], "laps": d["laps"],
                } for d in kal],
            }
            self.store.simpan_race(race)
            juara = next((d for d in kal if d["best_ms"]), None)
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
            }
