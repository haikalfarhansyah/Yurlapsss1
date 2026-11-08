"""Quick Race state machine; lap durations use decoder timestamps only."""
from __future__ import annotations

import math
import random
import threading
import time
import uuid
from typing import Optional

from .club import ClubStore
from .race_config import normalize


def kmh(loop_width_m, passing_us):
    if not loop_width_m or not passing_us or loop_width_m <= 0 or passing_us <= 0:
        return None
    value = loop_width_m * 3_600_000 / passing_us
    return round(value, 2) if math.isfinite(value) else None


URUTAN = {"position": "Posisi lintasan", "result": "Jumlah lap + total waktu",
          "lapsTime": "Jumlah lap + total waktu", "best": "Best lap",
          "top3": "Rata-rata 3 lap tercepat", "consecutive": "3 lap berurutan tercepat"}


class RaceEngine:
    def __init__(self, store: ClubStore):
        self.store = store
        self.lock = threading.RLock()
        presets = store.presets()
        self.cfg = normalize(presets[0] if presets else {})
        self.cfg["nama"] = self.cfg["name"]
        self.cfg["lap_storage_version"] = 2
        tracks = store.tracks()
        active = next((t for t in tracks if t.get("active")), None)
        self.track_id = (active or (tracks or [{}])[0]).get("id")
        self.cfg["min_lap_ms"] = int((active or (tracks or [{}])[0]).get("min_lap_ms") or 1500)
        self.peserta = []
        self.pilihan = None
        self.decoder_ts = None
        self._last_clock_ts = None
        self.decoder_received_mono = 0.0
        self._event_id = 0
        self.events = []
        self._crossing_seq = 0
        self._global_laps = 0
        self._best_lap_ms = None
        self._last_positions = {}
        self._clear_session()

    def _clear_driver(self, p):
        p.update(laps=[], passings=[], dur_us=None, terakhir_mono=0.0,
                 first_decoder_ts=None, last_decoder_ts=None, finish_decoder_ts=None,
                 finish_order=None, crossing_seq=None, last_crossing_seq=None,
                 last_race_crossing_ts=None, time_due=False, needs_baseline=False)

    def _clear_session(self):
        self.session_id = str(uuid.uuid4())
        self.status = "idle"
        self.mulai_mono = self.mulai_wall = 0.0
        self.akhir_wall = None
        self.start_decoder_ts = self.end_decoder_ts = None
        self._track_snapshot = self.race_track = None
        self.saved_id = None
        self.discarded = False
        self.alasan_selesai = ""
        self.timing_interrupted = False
        self.interrupted_at = self._paused_decoder_ts = None
        self._countdown_started = self._go_due = None
        self._last_countdown = None
        self._next_call = 0
        self._crossing_seq = 0
        self._global_laps = 0
        self._best_lap_ms = None
        self._global_best = None
        self._last_positions = {}
        self.log = []
        self.events = []
        for p in self.peserta:
            self._clear_driver(p)

    def _event(self, kind, **data):
        self._event_id += 1
        self.events.append(dict(id=self._event_id, kind=kind, **data))
        self.events = self.events[-100:]

    def _require_idle(self):
        if self.status != "idle":
            raise ValueError("Selesaikan, simpan atau buang sesi lalu tekan Reset terlebih dahulu")

    def _track(self):
        if self.status != "idle" and self._track_snapshot is not None:
            return self._track_snapshot
        return self.store.track(self.track_id)

    def set_cfg(self, data):
        with self.lock:
            self._require_idle()
            updated = normalize(data, self.cfg)
            updated["nama"] = updated["name"]
            updated["lap_storage_version"] = 2
            self.cfg = updated
            return dict(self.cfg)

    def set_track(self, track_id):
        with self.lock:
            self._require_idle()
            track = self.store.track(track_id)
            if not track:
                raise ValueError("Track tidak ditemukan")
            self.track_id = track_id
            self.cfg["min_lap_ms"] = int(track.get("min_lap_ms") or 1500)

    def set_peserta(self, daftar):
        with self.lock:
            self._require_idle()
            if not isinstance(daftar, list):
                raise ValueError("Peserta harus berupa daftar")
            members = {m["id"]: m for m in self.store.members()}
            out = []
            for i, source in enumerate(daftar, 1):
                if not isinstance(source, dict):
                    raise ValueError("Peserta harus berupa objek")
                mid = source.get("member_id") or source.get("id") or ""
                member = members.get(mid, {})
                tp = source.get("transponder")
                if tp in (None, ""):
                    tp = next(iter(member.get("transponder") or []), None)
                try:
                    tp = int(tp) if tp not in (None, "") else None
                    if tp is not None and not 0 <= tp <= 4294967295:
                        raise ValueError()
                except (TypeError, ValueError):
                    raise ValueError("ID transponder peserta tidak valid")
                name = str(member.get("name") or source.get("name") or "").strip()
                if not name:
                    raise ValueError("Nama peserta wajib diisi")
                p = dict(uid=source.get("uid") or str(uuid.uuid4()), no=i, member_id=mid,
                         account_id=member.get("account_id") or (mid if member.get("source") in ("cloud", "guest") else None),
                         name=name, nick=member.get("nick") or source.get("nick") or "",
                         tts_name=str(member.get("tts_name") or source.get("tts_name") or "").strip(),
                         avatar_url=member.get("avatar_url") or source.get("avatar_url") or "",
                         kelas=source.get("kelas") or member.get("kelas") or "",
                         transponder=tp, luar=not bool(member))
                self._clear_driver(p)
                out.append(p)
            ids = [p["transponder"] for p in out if p["transponder"] is not None]
            if len(ids) != len(set(ids)):
                raise ValueError("Satu transponder hanya boleh dipakai satu peserta")
            self.peserta = out
            self.pilihan = out[0]["uid"] if out else None
            return out

    def _pause_if_stale(self, now):
        # Decoder availability is advisory; missing passings remain missing.
        return False

    def observe_clock(self, timestamp):
        if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
            return
        with self.lock:
            now = time.monotonic()
            self._pause_if_stale(now)
            if self._last_clock_ts is not None and timestamp < self._last_clock_ts:
                self.timing_interrupted = True
                self._event("decoder_reset", message="Decoder restart; timing baseline direset")
                if self.status in ("countdown", "live"):
                    self.start_decoder_ts = timestamp
                for driver in self.peserta:
                    if driver.get("finish_decoder_ts") is None:
                        driver["last_decoder_ts"] = None
                        driver["needs_baseline"] = True
            self._last_clock_ts = timestamp
            self.decoder_ts = max(timestamp, self.decoder_ts if self.decoder_ts is not None else timestamp)
            self.decoder_received_mono = now

    def clock_reset(self):
        with self.lock:
            if self.status in ("live", "countdown", "paused"):
                self.timing_interrupted = True
                if self.decoder_ts is not None:
                    self.start_decoder_ts = self.decoder_ts
                for driver in self.peserta:
                    if driver.get("finish_decoder_ts") is None:
                        driver["last_decoder_ts"] = None
                        driver["needs_baseline"] = True
                self._event("decoder_reset", message="Decoder restart; sesi tetap berjalan dengan baseline baru")
            self.decoder_ts = None
            self._last_clock_ts = None
            self.decoder_received_mono = 0.0

    def resume(self):
        with self.lock:
            if self.status != "paused" or self.start_decoder_ts is None:
                raise ValueError("Sesi ini tidak dapat dilanjutkan; simpan atau buang lalu mulai ulang")
            pause_ts = self._paused_decoder_ts
            if pause_ts is None or self.decoder_ts is None:
                self.status = "live"
                self.interrupted_at = None
                self._event("resume")
                return self.snapshot()
            if self.decoder_ts < pause_ts:
                self.start_decoder_ts = self.decoder_ts
                self._paused_decoder_ts = self.decoder_ts
                self.status = "live"
                self.interrupted_at = None
                self._event("resume")
                return self.snapshot()
            # Exclude the disconnected interval from all deadlines. The first
            # later crossing is a new baseline, never a lap across the gap.
            shift = self.decoder_ts - pause_ts
            self.start_decoder_ts += shift
            for p in self.peserta:
                if p["finish_decoder_ts"] is None:
                    if p["first_decoder_ts"] is not None:
                        p["first_decoder_ts"] += shift
                    p["last_decoder_ts"] = None
                    p["needs_baseline"] = True
            self.status = "live"
            self.interrupted_at = None
            self._event("resume")
            return self.snapshot()

    def mulai(self):
        with self.lock:
            self._require_idle()
            track = self.store.track(self.track_id)
            if not track:
                raise ValueError("Buat dan aktifkan track sebelum memulai race")
            if not any(p["transponder"] is not None for p in self.peserta):
                raise ValueError("Pilih peserta dengan ID transponder terlebih dahulu")
            self._clear_session()
            self._track_snapshot = dict(track)
            self.race_track = dict(track)
            self.cfg["min_lap_ms"] = int(track.get("min_lap_ms") or 1500)
            self.status = "countdown"
            self.mulai_wall = time.time()
            self._countdown_started = time.monotonic()
            cd = self.cfg["cd"]
            delay = random.uniform(2, self.cfg["random_delay_max"]) if cd else 0
            step = self.cfg.get("countdown_step_ms", 1500) / 1000.0
            self._go_due = self._countdown_started + max(0, cd - 1) * step + delay
            self._countdown_tick()
            return self.snapshot()

    def _countdown_tick(self):
        if self.status != "countdown":
            return
        now = time.monotonic()
        step = self.cfg.get("countdown_step_ms", 1500) / 1000.0
        value = max(1, self.cfg["cd"] - int((now - self._countdown_started) / step)) if self.cfg["cd"] else 0
        if value and value != self._last_countdown:
            self._last_countdown = value
            self._event("countdown", value=value)
        if now >= self._go_due:
            self.go()

    def go(self):
        with self.lock:
            if self.status == "live":
                return self.snapshot()
            if self.status != "countdown":
                raise ValueError("Sesi belum siap dimulai")
            if time.monotonic() < self._go_due:
                return self.snapshot()
            # The decoder was validated in mulai(). Do not strand a countdown
            # when a status packet is momentarily late; the first later packet
            # is handled by the normal timing interruption guard.
            self.start_decoder_ts = self.decoder_ts
            self.status = "live"
            self.mulai_mono = time.monotonic()
            self.mulai_wall = time.time()
            self._event("go")
            self._stagger_calls()
            return self.snapshot()

    def _stagger_calls(self):
        if self.status != "live" or self.cfg["start_mode"] != "staggered" or self.cfg["stagger_mode"] != "auto":
            return
        participants = [p for p in self.peserta if p["transponder"] is not None]
        while self._next_call < len(participants) and self.elapsed_ms() >= self._next_call * self.cfg["stagger_gap_ms"]:
            p = participants[self._next_call]
            self._event("stagger_call", uid=p["uid"], transponder=p["transponder"], tts_name=p["tts_name"])
            self._next_call += 1

    def elapsed_ms(self):
        end = self.end_decoder_ts if self.status == "done" else self._paused_decoder_ts if self.status == "paused" else self.decoder_ts
        return max(0, end - self.start_decoder_ts) if self.start_decoder_ts is not None and end is not None else 0

    def sisa_ms(self):
        if self.cfg["target"] != "time":
            return None
        return max(0, self.cfg["target_time_ms"] - self.elapsed_ms())

    def _deadline(self, p):
        origin = self.start_decoder_ts if self.cfg["start_mode"] == "grid" else p["first_decoder_ts"]
        return origin + self.cfg["target_time_ms"] if origin is not None and self.cfg["target"] == "time" else None

    def catat(self, transponder, *, decoder_ts=None, rssi=None, hit_count=None, dur_us=None, **unused):
        with self.lock:
            self._pause_if_stale(time.monotonic())
            p = next((p for p in self.peserta if p["transponder"] == transponder), None)
            m = self.store.member_for_transponder(transponder) or {}
            entry = dict(transponder=transponder, decoder_timecode=decoder_ts, t=decoder_ts,
                         jam=str(decoder_ts) if decoder_ts is not None else "-",
                         nama=(p["nick"] or p["name"]) if p else (m.get("nick") or m.get("name") or f"TP {transponder}"),
                         rssi=rssi, hit_count=hit_count, dur_us=dur_us, lap=None, skip=True, kmh=None)
            self.log.insert(0, entry)
            self.log = self.log[:100]
            def reject(reason, kind="ignored"):
                entry.update(alasan=reason, status=kind)
                return dict(diterima=False, alasan=reason, entri=entry, pass_kind=kind)
            if isinstance(decoder_ts, bool) or not isinstance(decoder_ts, int) or decoder_ts < 0:
                return reject("Timestamp decoder tidak sah")
            if self.decoder_ts is None or decoder_ts > self.decoder_ts:
                self.decoder_ts = decoder_ts
            self.decoder_received_mono = time.monotonic()
            if not p:
                return reject("Bukan peserta")
            if p.get("finish_decoder_ts") is not None:
                return reject("Peserta sudah finish", "finished")
            if self.status != "live":
                return reject("Timing dijeda" if self.status == "paused" else "Sesi tidak berjalan")
            # A race may be started before the decoder has emitted its first
            # clock packet. Anchor the shared race clock to the first valid
            # passing instead of comparing an integer with None (which used
            # to abort the decoder callback and make the UI appear frozen).
            if self.start_decoder_ts is None:
                self.start_decoder_ts = decoder_ts
            if decoder_ts < self.start_decoder_ts:
                return reject("Lintasan sebelum GO")
            if (self.cfg["target"] == "time" and self.cfg["start_mode"] == "grid"
                    and p["first_decoder_ts"] is None and decoder_ts >= self._deadline(p)):
                self._mark_time_finished(p, self._deadline(p))
                self._check_finished()
                return reject("Waktu habis sebelum driver mulai", "finished")
            previous = p["last_decoder_ts"]
            if previous is not None and decoder_ts <= previous:
                return reject("Timestamp duplikat atau terlambat")
            lap_ms = decoder_ts - previous if previous is not None else None
            if lap_ms is not None and lap_ms < self.cfg["min_lap_ms"]:
                return reject("Di bawah minimum lap")
            deadline = self._deadline(p)
            if deadline is not None and decoder_ts > deadline and not self.cfg["finish_current_lap"]:
                self._mark_time_finished(p, deadline)
                self._check_finished()
                return reject("Waktu habis", "finished")
            origin = previous is None
            if p["first_decoder_ts"] is None:
                p["first_decoder_ts"] = decoder_ts
            p["last_decoder_ts"] = decoder_ts
            p["last_race_crossing_ts"] = decoder_ts
            p["needs_baseline"] = False
            p["dur_us"] = dur_us
            if not origin:
                p["laps"].append(lap_ms)
                self._global_laps += 1
            self._crossing_seq += 1
            p["crossing_seq"] = self._crossing_seq
            p["last_crossing_seq"] = self._crossing_seq
            speed = kmh((self.race_track or {}).get("loop_width_m"), dur_us)
            p["passings"].append(dict(decoder_timecode=decoder_ts, rssi=rssi, hit_count=hit_count,
                                      pass_duration_us=dur_us, speed_kmh=speed,
                                      lap_ms=lap_ms, origin=origin))
            entry.update(lap=len(p["laps"]), skip=False, kmh=speed, origin=origin, status="origin" if origin else "lap")
            deadline = self._deadline(p)
            finished = ((self.cfg["target"] == "laps" and len(p["laps"]) >= self.cfg["value"])
                        or (deadline is not None and decoder_ts >= deadline))
            if finished:
                p["finish_decoder_ts"] = decoder_ts
                p["finish_order"] = decoder_ts
            rows = self.klasemen()
            current = next((row for row in rows if row.get("uid") == p["uid"]), None)
            position = current.get("pos") if current else None
            previous_position = self._last_positions.get(p["uid"])
            if not origin and previous_position is not None and position != previous_position:
                self._event("position_change", uid=p["uid"], tts_name=p["tts_name"],
                            position=position, previous_position=previous_position,
                            transponder=transponder)
            if position is not None:
                self._last_positions[p["uid"]] = position
            if (not origin and self._best_lap_ms is None) or (not origin and lap_ms < self._best_lap_ms):
                self._best_lap_ms = lap_ms
                self._global_best = {"uid":p["uid"], "tts_name":p["tts_name"],
                                     "lap_ms":lap_ms, "global_lap":self._global_laps}
                self._event("best_lap", uid=p["uid"], tts_name=p["tts_name"],
                            lap_ms=lap_ms, global_lap=self._global_laps,
                            position=position, transponder=transponder)
            last_lap = (self.cfg["target"] == "laps" and not finished
                        and len(p["laps"]) == self.cfg["value"] - 1)
            if last_lap:
                self._event("last_lap", uid=p["uid"], tts_name=p["tts_name"],
                            position=position, transponder=transponder)
            if finished:
                self._event("driver_finish", uid=p["uid"], tts_name=p["tts_name"],
                            transponder=transponder,
                            position=(position if self.cfg["start_mode"] == "grid" and self.cfg["rank"] == "position" else None))
            crossing = dict(uid=p["uid"], tts_name=p["tts_name"], transponder=transponder,
                            lap=len(p["laps"]), lap_ms=lap_ms, position=position,
                            global_lap=self._global_laps, last_lap=last_lap, finished=finished,
                            origin=origin, position_changed=previous_position is not None and position != previous_position)
            if not origin:
                self._event("lap", **crossing)
            self._event("driver_passing", **crossing)
            done = self._check_finished()
            return dict(diterima=True, lap=len(p["laps"]), lap_ms=lap_ms, entri=entry, selesai=done,
                        pass_kind="origin" if origin else "lap", driver_finished=finished)

    def _check_finished(self):
        active = [p for p in self.peserta if p["transponder"] is not None]
        if self.status == "live" and active and all(p["finish_decoder_ts"] is not None for p in active):
            self._selesai("Semua driver menyelesaikan target")
            return True
        return False

    def _mark_time_finished(self, p, deadline):
        """Close one driver at a time-target boundary and announce it once."""
        if p.get("finish_decoder_ts") is not None:
            return False
        p.update(finish_decoder_ts=deadline, time_due=True)
        if p["first_decoder_ts"] is not None:
            position = next((row["pos"] for row in self.klasemen() if row["uid"] == p["uid"]), None)
            self._event("driver_finish", uid=p["uid"], tts_name=p["tts_name"],
                        transponder=p.get("transponder"),
                        position=position if self.cfg["start_mode"] == "grid" and self.cfg["rank"] == "position" else None)
        return True

    def cek_waktu(self):
        with self.lock:
            if self._pause_if_stale(time.monotonic()):
                return False
            if self.status == "countdown":
                self._countdown_tick()
            if self.status != "live":
                return False
            self._stagger_calls()
            if self.cfg["target"] == "time" and self.decoder_ts is not None:
                for p in self.peserta:
                    deadline = self._deadline(p)
                    if p["transponder"] is not None and deadline is not None and self.decoder_ts >= deadline:
                        p["time_due"] = True
                        if not self.cfg["finish_current_lap"] or p["first_decoder_ts"] is None:
                            self._mark_time_finished(p, deadline)
                return self._check_finished()
            return False

    def _selesai(self, reason):
        self.end_decoder_ts = self._paused_decoder_ts if self.status == "paused" else self.decoder_ts
        self.status = "done"
        self.akhir_wall = time.time()
        self.alasan_selesai = reason
        final_rows = self.klasemen()
        self._event("finish", message=reason,
                    results=[{"uid": row.get("uid"), "position": row.get("pos"),
                              "tts_name": row.get("tts_name") or ""}
                             for row in final_rows])

    def selesai(self, alasan="Dihentikan operator"):
        with self.lock:
            if self.status not in ("live", "paused", "countdown"):
                raise ValueError("Sesi tidak sedang berjalan")
            self._selesai(alasan)
            return self.snapshot()

    def klasemen(self):
        from .race_results import result_rows
        with self.lock:
            rows = result_rows([dict(p, laps=list(p["laps"]), passings=list(p["passings"])) for p in self.peserta], self.cfg)
            clock = self.end_decoder_ts if self.status == "done" else self._paused_decoder_ts if self.status == "paused" else self.decoder_ts
            for row in rows:
                deadline = self._deadline(row)
                origin = self.start_decoder_ts if self.cfg["start_mode"] == "grid" else row["first_decoder_ts"]
                end = row["finish_decoder_ts"] if row["finish_decoder_ts"] is not None else clock
                row["elapsed_ms"] = max(0, end-origin) if origin is not None and end is not None else None
                row["remaining_ms"] = max(0, deadline-clock) if deadline is not None and clock is not None else None
            return rows

    def simpan(self, nama: Optional[str] = None):
        with self.lock:
            if self.saved_id:
                race = self.store.race(self.saved_id)
                return dict(id=self.saved_id, nama=race["name"], jumlah_lap=sum(len(p["laps"]) for p in self.peserta), pembalap=len(self.peserta))
            if self.discarded or self.status not in ("done", "live", "paused"):
                raise ValueError("Belum ada sesi untuk disimpan")
            if self.status != "done":
                self._selesai("Disimpan operator")
            track = self.race_track or self._track() or {}
            rows = self.klasemen()
            for row in rows:
                row["result_status"] = "DNS" if row["first_decoder_ts"] is None else "FIN" if row["finish_decoder_ts"] is not None else "DNF"
            rid = str(uuid.uuid4())
            name = str(nama or "").strip() or f"{self.cfg['nama']} #{len(self.store.races()) + 1}"
            race = dict(id=rid, name=name, track_id=self.track_id, track_name=track.get("name"),
                        track_width=track.get("width_m"), cfg=dict(self.cfg), started_at=self.mulai_wall or time.time(),
                        ended_at=self.akhir_wall, status="saved", sync="belum", start_decoder_ts=self.start_decoder_ts,
                        end_decoder_ts=self.end_decoder_ts, loop_width_m=track.get("loop_width_m"),
                        finish_reason=self.alasan_selesai, timing_interrupted=self.timing_interrupted,
                        drivers=rows, log=list(self.log))
            self.store.simpan_race(race)
            winner = min((d for d in rows if d["best_ms"] is not None), key=lambda d:d["best_ms"], default=None)
            if winner and not self.timing_interrupted:
                self.store.perbarui_rekor(self.track_id, winner["best_ms"], winner["nick"] or winner["name"])
            self.saved_id = rid
            return dict(id=rid, nama=name, jumlah_lap=sum(d["n_lap"] for d in rows), pembalap=len(rows))

    def buang(self):
        with self.lock:
            if self.status == "idle":
                raise ValueError("Tidak ada sesi untuk dibuang")
            if self.saved_id:
                raise ValueError("Sesi sudah disimpan; hapus melalui History")
            if self.status != "done":
                self._selesai("Dibuang operator")
            self.discarded = True

    def reset(self):
        with self.lock:
            if self.status != "idle" and (self.status != "done" or not (self.saved_id or self.discarded)):
                raise ValueError("Simpan atau buang sesi sebelum Reset")
            self._clear_session()
            return self.snapshot()

    def snapshot(self):
        with self.lock:
            countdown = self.status == "countdown"
            return dict(status=self.status, session_id=self.session_id, cfg=dict(self.cfg), track_id=self.track_id, track=self._track(),
                        peserta=[dict(p, laps=list(p["laps"]), passings=list(p["passings"])) for p in self.peserta],
                        klasemen=self.klasemen(), log=list(self.log[:60]), elapsed_ms=self.elapsed_ms(), sisa_ms=self.sisa_ms(),
                        pilihan=self.pilihan, alasan_selesai=self.alasan_selesai, start_decoder_ts=self.start_decoder_ts,
                        clock_stale=time.monotonic()-self.decoder_received_mono > 3,
                        timing_interrupted=self.timing_interrupted, interrupted=self.status == "paused",
                        saved_id=self.saved_id, discarded=self.discarded,
                        can_reset=self.status == "done" and bool(self.saved_id or self.discarded),
                        countdown_value=self._last_countdown if countdown else None,
                        countdown_remaining_ms=max(0,int((self._go_due-time.monotonic())*1000)) if countdown else 0,
                        global_laps=self._global_laps, global_best=dict(self._global_best) if self._global_best else None,
                        events=list(self.events))
