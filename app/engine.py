"""Mesin waktu (timing engine) YurLaps.

Aturan main (mirip perilaku lap counter OpenStint/LapBeeps untuk sesi latihan):

* Setiap transponder punya "stint" sendiri.
* Lintasan pertama setelah idle membuka stint baru dan menjadi titik nol
  (tidak dihitung sebagai lap).
* Lintasan berikutnya = lap. Selisih waktu = waktu lap.
* Idle lebih dari ``idle_split_seconds`` menutup stint otomatis; lintasan
  berikutnya membuka stint baru.
* Lap yang lebih cepat dari ``min_lap_seconds`` diabaikan (anti-ghost).

Semua perhitungan durasi memakai ``time.monotonic()`` supaya tidak terpengaruh
perubahan jam sistem (NTP).
"""
from __future__ import annotations

import itertools
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from .config import APP_VERSION, Config


# ---------------------------------------------------------------------------
# util
# ---------------------------------------------------------------------------
def fmt_ms(ms: Optional[int]) -> str:
    if ms is None:
        return "--:--.---"
    ms = int(ms)
    m, rem = divmod(ms, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{m}:{s:02d}.{milli:03d}"


def fmt_ms_short(ms: Optional[int]) -> str:
    """8.452 / 1:12.308 (tanpa menit kalau nol)."""
    if ms is None:
        return "—"
    ms = int(ms)
    if ms < 60_000:
        s, milli = divmod(ms, 1000)
        return f"{s}.{milli:03d}"
    return fmt_ms(ms)


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------
@dataclass
class Passing:
    transponder_id: int
    transponder_type: str = "OPN"
    rssi: Optional[float] = None
    hit_count: Optional[int] = None
    pass_duration_us: Optional[int] = None
    mer: Optional[float] = None
    decoder_timecode: Optional[int] = None     # ts monotonik decoder (ms)
    raw: str = ""                              # baris asli dari decoder

    @property
    def speed_kmh(self) -> Optional[float]:
        """Estimasi kecepatan dari lama transponder di dalam loop.

        Butuh lebar loop (cm) untuk hasil absolut, jadi di sini hanya
        durasi mentah yang disimpan; kecepatan dihitung di UI.
        """
        return None


@dataclass
class Lap:
    seq: int
    time_ms: int
    at: float                    # wall clock (epoch seconds)
    monotonic: float
    rssi: Optional[float] = None
    mer: Optional[float] = None
    pass_duration_us: Optional[int] = None
    hit_count: Optional[int] = None            # jumlah frame valid dalam lintasan
    decoder_timecode: Optional[int] = None     # jam monotonik decoder (ms)

    def to_dict(self) -> dict:
        return {
            "seq": self.seq,
            "time_ms": self.time_ms,
            "at": self.at,
            "rssi": self.rssi,
            "mer": self.mer,
            "pass_duration_us": self.pass_duration_us,
            "hit_count": self.hit_count,
            "decoder_timecode": self.decoder_timecode,
        }


@dataclass
class Stint:
    id: int
    transponder_id: int
    transponder_type: str
    started_at: float
    laps: list[Lap] = field(default_factory=list)
    ended_at: Optional[float] = None
    index: int = 1               # stint ke-berapa untuk pembalap ini
    session_id: int = 0

    # --- statistik --------------------------------------------------------
    @property
    def lap_count(self) -> int:
        return len(self.laps)

    @property
    def best_lap(self) -> Optional[Lap]:
        return min(self.laps, key=lambda l: l.time_ms) if self.laps else None

    @property
    def last_lap(self) -> Optional[Lap]:
        return self.laps[-1] if self.laps else None

    @property
    def total_ms(self) -> int:
        return sum(l.time_ms for l in self.laps)

    @property
    def average_ms(self) -> Optional[int]:
        return int(self.total_ms / len(self.laps)) if self.laps else None

    @property
    def duration(self) -> Optional[float]:
        if self.ended_at is not None:
            return self.ended_at - self.started_at
        if self.last_lap:
            return self.last_lap.at - self.started_at
        return 0.0

    def to_dict(self, compact: bool = False) -> dict:
        laps = self.laps if not compact else self.laps[-30:]
        return {
            "id": self.id,
            "index": self.index,
            "transponder_id": self.transponder_id,
            "transponder_type": self.transponder_type,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "open": self.ended_at is None,
            "lap_count": self.lap_count,
            "best_ms": self.best_lap.time_ms if self.best_lap else None,
            "last_ms": self.last_lap.time_ms if self.last_lap else None,
            "average_ms": self.average_ms,
            "duration": self.duration,
            "laps": [l.to_dict() for l in laps],
        }


@dataclass
class Driver:
    transponder_id: int
    transponder_type: str = "OPN"
    driver: str = ""
    car: str = ""
    class_name: str = ""
    note: str = ""
    stints: list[Stint] = field(default_factory=list)
    last_pass_mono: Optional[float] = None
    last_pass_ts: Optional[int] = None      # timestamp decoder (ms) lintasan terakhir
    last_pass_at: Optional[float] = None
    last_lap_ms: Optional[int] = None
    passes: int = 0

    @property
    def key(self) -> str:
        return str(self.transponder_id)

    @property
    def name(self) -> str:
        return self.driver or f"#{self.transponder_id}"

    @property
    def current(self) -> Optional[Stint]:
        if self.stints and self.stints[-1].ended_at is None:
            return self.stints[-1]
        return None

    def all_laps(self) -> list[Lap]:
        return [l for s in self.stints for l in s.laps]

    @property
    def best_lap(self) -> Optional[Lap]:
        laps = self.all_laps()
        return min(laps, key=lambda l: l.time_ms) if laps else None

    @property
    def total_laps(self) -> int:
        return sum(s.lap_count for s in self.stints)

    def to_dict(self, compact: bool = True) -> dict:
        best = self.best_lap
        return {
            "transponder_id": self.transponder_id,
            "transponder_type": self.transponder_type,
            "driver": self.driver,
            "car": self.car,
            "class_name": self.class_name,
            "note": self.note,
            "name": self.name,
            "passes": self.passes,
            "total_laps": self.total_laps,
            "best_ms": best.time_ms if best else None,
            "last_ms": self.last_lap_ms,
            "stint_index": self.current.index if self.current else (self.stints[-1].index if self.stints else 0),
            "stints": [s.to_dict(compact=compact) for s in self.stints],
        }


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------
class Engine:
    def __init__(self, cfg: Config, storage=None, on_event=None):
        self.cfg = cfg
        self.storage = storage
        self.on_event = on_event          # callable(event: dict) -> None
        self.drivers: dict[int, Driver] = {}
        self.events: deque[dict] = deque(maxlen=300)

        self._wall0 = time.time()
        self._mono0 = time.monotonic()
        self._stint_ids = itertools.count(1)
        self._session_ids = itertools.count(1)

        self.session_id = 0
        self.session_started_at = self._wall0
        self.session_name = cfg.event_name or "Practice"

        self.decoder_online = False
        self.decoder_last_msg = 0.0       # monotonic
        self.decoder_status_extra: dict = {}
        # --- diagnostik decoder (dipakai panel Decoder) --------------------
        self.noise_history: deque = deque(maxlen=240)   # (mono, noise, dc, jitter, frames)
        self.pass_log: deque = deque(maxlen=400)        # semua lintasan mentah
        self.pass_counters: dict = {"P": 0, "ghost": 0, "bad": 0, "S": 0, "T": 0,
                                    "start": 0, "lap": 0}
        self._decoder_ts_hint: Optional[int] = None   # ts terakhir dari pesan S
        self.device_info: dict = {}
        self.decoder_status: dict = {}
        self.last_passing_at: Optional[float] = None
        self.version = 0                  # naik setiap state berubah
        self._flash: Optional[dict] = None

        self._bootstrap()

    # -- waktu -------------------------------------------------------------
    def now(self) -> float:
        return self._wall0 + (time.monotonic() - self._mono0)

    def mono(self) -> float:
        return time.monotonic()

    # -- inisialisasi ------------------------------------------------------
    def _bootstrap(self) -> None:
        # muat mapping transponder -> pembalap dari config
        for key, m in self.cfg.mappings.items():
            try:
                d = Driver(
                    transponder_id=int(m.transponder_id),
                    transponder_type=m.transponder_type or "OPN",
                    driver=m.driver,
                    car=m.car,
                    class_name=m.class_name,
                    note=m.note,
                )
                self.drivers[d.transponder_id] = d
            except Exception:
                continue

        if self.storage:
            try:
                sid = self.storage.open_session(
                    name=self.session_name,
                    track=self.cfg.track_name,
                    event=self.cfg.event_name,
                    started_at=self.session_started_at,
                )
                self.session_id = sid
                self._session_ids = itertools.count(sid + 1)
                self._restore_recent()
            except Exception as exc:  # pragma: no cover
                self.log("error", f"Gagal membuka database: {exc}")

        self.log("info", f"YurLaps siap · sesi “{self.session_name}”")

    def _restore_recent(self) -> None:
        """Ambil kembali stint yang belum ditutup (mis. aplikasi ditutup paksa)."""
        if not self.storage:
            return
        rows = self.storage.recover_open_stints(self.session_id)
        for row in rows:
            d = self.driver(row["transponder_id"])
            st = Stint(
                id=row["id"],
                transponder_id=row["transponder_id"],
                transponder_type=row.get("transponder_type") or "OPN",
                started_at=row["started_at"],
                ended_at=row["ended_at"],
                index=row.get("stint_index") or 1,
                session_id=self.session_id,
            )
            d.stints.append(st)
        if rows:
            self.log("info", f"{len(rows)} stint lama dipulihkan dari sesi sebelumnya")

    # -- util --------------------------------------------------------------
    def log(self, level: str, message: str, **extra) -> None:
        ev = {"kind": "log", "level": level, "message": message, "at": self.now(), **extra}
        self.events.append(ev)
        self.version += 1
        if self.on_event:
            self.on_event(ev)

    def _emit(self, kind: str, **data) -> None:
        ev = {"kind": kind, "at": self.now(), **data}
        self.events.append(ev)
        self.version += 1
        if self.on_event:
            self.on_event(ev)

    # -- driver ------------------------------------------------------------
    def driver(self, transponder_id: int, transponder_type: str = "OPN") -> Driver:
        tid = int(transponder_id)
        d = self.drivers.get(tid)
        if d is None:
            m = self.cfg.mapping_for(tid)
            d = Driver(
                transponder_id=tid,
                transponder_type=transponder_type or "OPN",
                driver=m.driver,
                car=m.car,
                class_name=m.class_name,
                note=m.note,
            )
            self.drivers[tid] = d
            self.version += 1
        elif transponder_type and d.transponder_type != transponder_type:
            d.transponder_type = transponder_type
        return d

    def update_driver(self, transponder_id: int, **fields) -> None:
        d = self.driver(transponder_id)
        for k in ("driver", "car", "class_name", "note", "transponder_type"):
            if k in fields and fields[k] is not None:
                setattr(d, k, str(fields[k]))
        m = self.cfg.mapping_for(transponder_id)
        m.driver, m.car, m.class_name, m.note = d.driver, d.car, d.class_name, d.note
        m.transponder_type = d.transponder_type or "OPN"
        self.cfg.save()
        self._persist_driver(d)
        self.version += 1

    def remove_driver(self, transponder_id: int) -> None:
        """Hapus pemetaan transponder→pembalap (nama saja, data lap tetap).

        Dipakai menu Driver (tombol Hapus). Lap yang sudah tercatat tidak
        dihapus: riwayat tetap utuh dan bisa diekspor.
        """
        tid = int(transponder_id)
        self.cfg.mappings.pop(str(tid), None)
        self.cfg.save()
        if self.storage:
            try:
                self.storage.delete_driver(tid)
            except Exception:
                pass
        d = self.drivers.get(tid)
        if d is not None:
            d.driver = ""
        self.log("info", f"Pemetaan pembalap untuk #{tid} dihapus")
        self.version += 1

    def _persist_driver(self, d: Driver) -> None:
        if self.storage:
            self.storage.save_driver(d)

    # -- decoder -----------------------------------------------------------
    def decoder_seen(self) -> None:
        self.decoder_last_msg = self.mono()
        if not self.decoder_online:
            self.decoder_online = True
            self.log("ok", "Decoder terhubung")
            self._emit("decoder", online=True)

    def on_timesync(self, parts: list[str]) -> None:
        """Pesan 'T' (time sync) dari transponder OpenStint.

        Berguna untuk sector timing multi-decoder; di YurLaps hanya dicatat
        sebagai bukti decoder hidup + jam acuannya.
        """
        self.decoder_seen()
        self.pass_counters["T"] = self.pass_counters.get("T", 0) + 1
        st = self.decoder_status or {}
        st["timesync"] = {
            "at": self.now(),
            "transponder_type": parts[2] if len(parts) > 2 else "",
            "transponder_id": parts[3] if len(parts) > 3 else "",
            "transponder_timecode": parts[4] if len(parts) > 4 else "",
        }
        self.decoder_status = st
        self.version += 1

    def decoder_status_message(self, parts: list[str]) -> None:
        """Pesan 'S' dari OpenStint."""
        self.decoder_seen()
        try:
            st = {
                "noise_power": float(parts[2]),
                "dc_offset": float(parts[3]),
                "frames_received": int(parts[4]),
                "frames_processed": int(parts[5]),
                "jitter_p95_ms": float(parts[6]) if len(parts) > 6 else None,
                "at": self.now(),
                "mono": self.mono(),
            }
        except (IndexError, ValueError):
            return
        self.decoder_status = st
        self.pass_counters["S"] += 1
        if len(parts) > 1:
            try:
                st["timecode"] = int(parts[1])
                self._decoder_ts_hint = int(parts[1])
            except ValueError:
                pass
        self.noise_history.append(
            (st["mono"], st["noise_power"], st["dc_offset"],
             st.get("jitter_p95_ms"), st.get("frames_received")))
        self._emit("noise", **{k: st.get(k) for k in
                               ("noise_power", "dc_offset", "frames_received",
                                "frames_processed", "jitter_p95_ms", "timecode")})
        self.version += 1

    def check_decoder_alive(self, timeout: float = 6.0) -> None:
        if self.decoder_online and self.mono() - self.decoder_last_msg > timeout:
            self.decoder_online = False
            self.log("warn", "Decoder tidak mengirim data (>6 s)")
            self._emit("decoder", online=False)

    # -- lintasan ----------------------------------------------------------
    def on_passing(self, p: Passing) -> Optional[Lap]:
        """Proses satu lintasan transponder. Kembalikan Lap kalau valid."""
        self.decoder_seen()
        d = self.driver(p.transponder_id, p.transponder_type)
        now_mono = self.mono()
        now_wall = self.now()

        d.passes += 1
        d.transponder_type = p.transponder_type or d.transponder_type
        self.pass_counters["P"] += 1
        self.last_passing_at = now_wall
        if p.decoder_timecode is None:
            p.decoder_timecode = self._decoder_ts_hint

        stint = d.current
        lap: Optional[Lap] = None

        if stint is None:
            # buka stint baru, lintasan ini = titik nol (atau lap 1 dari stint
            # sebelumnya yang sudah ditutup karena idle)
            stint = self._open_stint(d, now_wall)
            d.last_pass_mono = now_mono
            d.last_pass_ts = p.decoder_timecode
            d.last_pass_at = now_wall
            self.log("info", f"{d.name}: stint #{stint.index} dimulai")
            self.log_pass(p, d, "start", note=f"titik nol stint #{stint.index}")
            self.version += 1
            return None

        delta_ms = self.lap_delta_ms(d, p, now_mono, now_wall)

        if delta_ms < int(self.cfg.min_lap_seconds * 1000):
            # terlalu cepat: kemungkinan pantulan/ghost. Jangan ubah acuan waktu.
            self.log("warn", f"{d.name}: lintasan ganda diabaikan ({delta_ms} ms)")
            self.log_pass(p, d, "ghost", lap_ms=delta_ms,
                          note=f"diabaikan (< {self.cfg.min_lap_seconds:g} s)")
            # ghost tidak boleh menggeser titik acuan (timestamp tetap yang lama)
            return None

        lap = Lap(
            seq=stint.lap_count + 1,
            time_ms=delta_ms,
            at=now_wall,
            monotonic=now_mono,
            rssi=p.rssi,
            mer=p.mer,
            pass_duration_us=p.pass_duration_us,
            hit_count=p.hit_count,
            decoder_timecode=p.decoder_timecode,
        )
        stint.laps.append(lap)
        d.last_pass_mono = now_mono
        d.last_pass_ts = p.decoder_timecode
        d.last_pass_at = now_wall
        d.last_lap_ms = delta_ms
        self.last_passing_at = now_wall

        best = stint.best_lap
        is_best = best is not None and best.seq == lap.seq
        if self.storage:
            try:
                self.storage.save_lap(stint, lap, d)
            except Exception as exc:  # pragma: no cover
                self.log("error", f"Gagal menyimpan lap: {exc}")

        self.log("lap", f"{d.name} · lap {lap.seq} · {fmt_ms_short(delta_ms)}"
                        + ("  ★ best" if is_best else ""))
        self._emit(
            "lap",
            transponder_id=d.transponder_id,
            driver=d.name,
            stint_id=stint.id,
            seq=lap.seq,
            time_ms=delta_ms,
            best=is_best,
            stint_best_ms=stint.best_lap.time_ms if stint.best_lap else None,
            rssi=p.rssi, mer=p.mer, hit_count=p.hit_count,
            pass_duration_us=p.pass_duration_us,
            decoder_timecode=p.decoder_timecode,
        )
        self.log_pass(p, d, "lap", lap_ms=delta_ms, seq=lap.seq,
                      note="★ best" if is_best else "")
        self.version += 1
        return lap

    def lap_delta_ms(self, d: "Driver", p: Passing, now_mono: float,
                     now_wall: float) -> int:
        """Selisih waktu lintasan, diambil dari timestamp decoder OpenStint.

        Protokol OpenStint: ``P <timestamp> ...`` = milidetik dari jam monotonik
        decoder (sejak openstint.exe dijalankan). Selisih dua timestamp itulah
        lap time yang paling akurat, jadi itulah yang dipakai.

        Cadangan: kalau timestamp tidak ada (decoder lain / data lama) atau
        decoder baru saja restart (timestamp mundur), pakai jam komputer.
        """
        ts = p.decoder_timecode
        prev = d.last_pass_ts
        wall_ms = int(round((now_mono - d.last_pass_mono) * 1000)) \
            if d.last_pass_mono is not None else 0
        if ts is not None and prev is not None:
            delta = int(ts) - int(prev)
            if delta > 0:
                return delta
            # timestamp mundur -> decoder restart di tengah stint
            self.log("warn", "Timestamp decoder mundur (openstint dijalankan ulang) "
                             "- lap dihitung dari jam komputer")
        return wall_ms

    def log_pass(self, p: Passing, driver: "Driver", kind: str,
                 lap_ms: Optional[int] = None, seq: Optional[int] = None,
                 note: str = "") -> dict:
        """Simpan satu lintasan mentah untuk tabel 'Lintasan terakhir'."""
        entry = {
            "at": self.now(),
            "transponder_id": p.transponder_id,
            "transponder_type": p.transponder_type,
            "rssi": p.rssi,
            "mer": p.mer,
            "hit_count": p.hit_count,
            "pass_duration_us": p.pass_duration_us,
            "decoder_timecode": p.decoder_timecode,
            "driver": driver.driver if driver else "",
            "name": driver.name if driver else f"#{p.transponder_id}",
            "pass_kind": kind,      # start | lap | ghost
            "seq": seq,
            "lap_ms": lap_ms,
            "note": note,
            "raw": p.raw,
        }
        self.pass_log.append(entry)
        self.pass_counters[kind] = self.pass_counters.get(kind, 0) + 1
        # 'kind' dipakai _emit sebagai jenis event -> field tabel memakai pass_kind
        self._emit("passing", **{k: v for k, v in entry.items() if k != "raw"})
        return entry

    def _open_stint(self, d: Driver, wall_now: float) -> Stint:
        idx = (d.stints[-1].index + 1) if d.stints else 1
        st = Stint(
            id=next(self._stint_ids),
            transponder_id=d.transponder_id,
            transponder_type=d.transponder_type or "OPN",
            started_at=wall_now,
            index=idx,
            session_id=self.session_id,
        )
        d.stints.append(st)
        if len(d.stints) > 30:
            del d.stints[: len(d.stints) - 30]
        if self.storage:
            try:
                st.id = self.storage.create_stint(st, d)
            except Exception as exc:  # pragma: no cover
                self.log("error", f"Gagal menyimpan stint: {exc}")
        return st

    def close_stint(self, transponder_id: int, reason: str = "manual") -> None:
        d = self.driver(transponder_id)
        st = d.current
        if not st:
            return
        st.ended_at = self.now()
        d.last_pass_mono = None
        d.last_pass_ts = None
        if self.storage:
            try:
                self.storage.close_stint(st)
            except Exception:
                pass
        self.log("info", f"{d.name}: stint #{st.index} ditutup ({reason}) "
                         f"· {st.lap_count} lap")
        self._emit("stint", transponder_id=d.transponder_id, closed=st.id)

    def tick(self) -> None:
        """Panggil berkala: tutup stint yang idle, cek decoder."""
        self.check_decoder_alive()
        cutoff = self.cfg.idle_split_seconds
        now = self.mono()
        changed = False
        for d in self.drivers.values():
            st = d.current
            if not st:
                continue
            last = d.last_pass_mono
            if last is None:
                # stint tanpa lap sama sekali: tutup setelah idle juga
                last_mono = st.started_at
                idle = now - (self._mono0 + (last_mono - self._wall0))
            else:
                idle = now - last
            if idle > cutoff:
                self.close_stint(d.transponder_id, reason=f"idle {int(idle)} s")
                changed = True
        if changed:
            self.version += 1

    # -- sesi --------------------------------------------------------------
    def new_session(self, name: str = "") -> None:
        for d in list(self.drivers.values()):
            if d.current:
                self.close_stint(d.transponder_id, reason="sesi baru")
        self.session_started_at = self.now()
        self.session_name = name or self.cfg.event_name or "Practice"
        if self.storage:
            try:
                self.session_id = self.storage.close_session_and_open(
                    self.session_id, self.session_name, self.cfg.track_name,
                    self.cfg.event_name, self.session_started_at)
            except Exception:
                self.session_id += 1
        else:
            self.session_id += 1
        self.log("info", f"Sesi baru dimulai: “{self.session_name}”")
        self.version += 1

    # -- snapshot ----------------------------------------------------------
    def snapshot(self) -> dict:
        drivers = [d.to_dict() for d in self.drivers.values()]
        drivers.sort(key=lambda x: (x["best_ms"] is None, x["best_ms"] or 0))
        return {
            "version": self.version,
            "app": {"name": "YurLaps", "version": APP_VERSION,
                    "session_name": self.session_name,
                    "session_id": self.session_id,
                    "track": self.cfg.track_name, "event": self.cfg.event_name,
                    "session_started_at": self.session_started_at,
                    "now": self.now()},
            "decoder": {
                "online": self.decoder_online,
                "endpoint": self.cfg.zmq_endpoint,
                "name": self.cfg.decoder_name,
                "status": self.decoder_status,
                "manager": self.decoder_status_extra,
                "device": self.device_info,
                "noise_history": [{"t": t, "noise": n, "dc": dc, "jitter": j, "frames": f}
                                  for (t, n, dc, j, f) in list(self.noise_history)[-120:]],
                "passes": list(self.pass_log)[-60:],
                "counters": dict(self.pass_counters),
                "last_passing_at": self.last_passing_at,
            },
            "settings": {
                "idle_split_seconds": self.cfg.idle_split_seconds,
                "min_lap_seconds": self.cfg.min_lap_seconds,
                "beep_on_lap": self.cfg.beep_on_lap,
                "announce_lap_times": self.cfg.announce_lap_times,
                "beep_volume": self.cfg.beep_volume,
                "manage_decoder": self.cfg.manage_decoder,
                "decoder_args": self.cfg.decoder_args,
                "decoder_gain": self.cfg.decoder_gain,
                "decoder_bias_tee": self.cfg.decoder_bias_tee,
                "decoder_direct_sampling": self.cfg.decoder_direct_sampling,
                "decoder_device_serial": self.cfg.decoder_device_serial,
                "decoder_use_system_clock": self.cfg.decoder_use_system_clock,
                "decoder_monitor": self.cfg.decoder_monitor,
                "decoder_auto_download": self.cfg.decoder_auto_download,
                "driver_vid": self.cfg.driver_vid,
                "driver_pid": self.cfg.driver_pid,
                "auto_check_updates": self.cfg.auto_check_updates,
                "update_manifest_url": self.cfg.update_manifest_url,
                "online_enabled": self.cfg.online_enabled,
                "online_mode": self.cfg.online_mode,
                "online_url": self.cfg.online_url,
                "access_token": bool(self.cfg.access_token),
            },
            "drivers": drivers,
            "events": list(self.events)[-60:],
        }

    def online_payload(self) -> dict:
        """Data ringkas untuk dikirim ke website hasil online."""
        drivers = []
        for d in self.drivers.values():
            if d.total_laps == 0 and d.passes == 0:
                continue
            cur = d.current or (d.stints[-1] if d.stints else None)
            best = d.best_lap
            drivers.append({
                "transponder_id": d.transponder_id,
                "transponder_type": d.transponder_type,
                "driver": d.driver,
                "car": d.car,
                "class_name": d.class_name,
                "note": d.note,
                "total_laps": d.total_laps,
                "best_ms": best.time_ms if best else None,
                "last_ms": d.last_lap_ms,
                "stint_index": cur.index if cur else 0,
                "stint_open": bool(d.current),
                "stint_started_at": cur.started_at if cur else None,
                "last_pass_at": d.last_pass_at,
            })
        drivers.sort(key=lambda x: (x["best_ms"] is None, x["best_ms"] or 0))
        return {
            "instance": self.cfg.track_name,
            "track": self.cfg.track_name,
            "event": self.cfg.event_name,
            "session": {
                "id": self.session_id,
                "name": self.session_name,
                "started_at": self.session_started_at,
            },
            "sent_at": self.now(),
            "drivers": drivers,
        }
