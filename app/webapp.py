"""Server HTTP lokal YurLaps (tanpa framework, hanya stdlib).

* Menyajikan UI di ``/``
* API JSON di ``/api/*``
* Server-Sent Events di ``/api/events`` untuk update realtime
* Endpoint ``/api/health`` dipakai untuk "cari decoder/instance di LAN"

Kalau ``config.access_token`` diisi, semua endpoint (kecuali /api/health dan
file statis) butuh header ``X-YurLaps-Token`` (alias lama: ``X-StintLab-Token``) atau
query ``?t=<token>``.
"""
from __future__ import annotations

import csv
import ipaddress
import io
import json
import os
import sys
import mimetypes
import base64
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, parse_qs

from .config import APP_VERSION, Config
from .engine import Engine
from .net import lan_urls
from .race_upload import UploadError

def _static_dir() -> Path:
    """Folder UI web. Saat dibundel PyInstaller, file UI ada di _internal/app/static."""
    here = Path(__file__).parent / "static"
    if here.is_dir():
        return here
    base = Path(getattr(sys, "_MEIPASS", "")) / "app" / "static"
    return base if base.is_dir() else here


STATIC_DIR = _static_dir()


class EventBus:
    """Fan-out event engine ke banyak klien SSE."""

    def __init__(self) -> None:
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=200)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, event: dict) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except Exception:
                    pass


class AppContext:
    def __init__(self, cfg: Config, engine: Engine, storage, bus: EventBus):
        self.cfg = cfg
        self.engine = engine
        self.storage = storage
        self.bus = bus
        # --- Quick Race offline ------------------------------------------
        # Pemilik tunggal penyimpanan club dan mesin sesi. WAJIB di sini
        # (bukan per-request): handler HTTP dibuat ulang tiap koneksi,
        # sehingga state sesi akan hilang kalau disimpan di handler.
        self._club = None
        self._race = None
        self._cloud = None
        self._retired_stores = []
        # RLock: properti race membuat RaceEngine lewat properti club
        self._club_lock = threading.RLock()
        self.decoder_manager = None
        self.decoder_client = None
        self.updater = None
        self.update_pending = False
        self.guest_capture_until = 0.0
        self.guest_capture_result = None
        from .practice import PracticeRecorder
        self.practice = PracticeRecorder(on_close=self._persist_practice,
                                         idle_seconds=getattr(cfg, "idle_split_seconds", 120))
        self._last_practice_persist = 0.0
        self.start_time = time.time()
        previous_event = engine.on_event
        def handle_timing_event(event):
            # Practice is automatic while Quick Race is idle.
            self.practice.tick(race_status=self.race.status, track=self.race._track(),
                               idle_seconds=getattr(self.cfg, "idle_split_seconds", 120))
            if event.get("kind") == "passing":
                self.club.observe_transponder(event["transponder_id"])
                capturing = (self.race.status == "idle" and
                             time.monotonic() < self.guest_capture_until)
                if capturing:
                    self.guest_capture_until = 0.0
                    self.guest_capture_result = {"transponder": int(event["transponder_id"]),
                                                 "at": time.time()}
                    if self._cloud is not None:
                        self._cloud.wake.set()
                practice_result = None
                if self.race.status == "idle" and not capturing:
                    member = self.club.member_for_transponder(event.get("transponder_id"))
                    practice_result = self.practice.record(
                        event.get("transponder_id"), event.get("decoder_timecode"),
                        member=member,
                        rssi=event.get("rssi"), hit_count=event.get("hit_count"),
                        dur_us=event.get("pass_duration_us"),
                    )
                    self._maybe_persist_practice()
                result = None if capturing else self.race.catat(event["transponder_id"],
                                         decoder_ts=event.get("decoder_timecode"),
                                         rssi=event.get("rssi"), hit_count=event.get("hit_count"),
                                         dur_us=event.get("pass_duration_us"))
                # One authoritative event per raw decoder passing.  The UI
                # uses this for the immediate beep in both practice and race;
                # its result must come from the active recorder in practice.
                self.bus.publish({"kind": "race_passing", "at": time.time(),
                                  "source": "practice" if self.race.status == "idle" else "race",
                                  "transponder_id": event.get("transponder_id"),
                                  "driver": event.get("driver") or "",
                                  "decoder_timecode": event.get("decoder_timecode"),
                                  "result": ({"status": "registration", "skip": True} if capturing else
                                             practice_result if self.race.status == "idle" else (result or {}))})
            elif event.get("kind") == "noise":
                self.race.observe_clock(event.get("timecode"))
            elif event.get("kind") == "clock_reset":
                self.race.clock_reset()
                self.practice.clock_reset()
            if previous_event:
                previous_event(event)
        def route_event(event):
            if event.get("kind") not in ("passing", "noise", "clock_reset"):
                if previous_event:
                    previous_event(event)
                return
            # All paths acquire club before practice, including account
            # switches. This keeps callbacks from saving into the next club.
            with self._club_lock:
                handle_timing_event(event)
        engine.on_event = route_event

    def _maybe_persist_practice(self) -> None:
        """Checkpoint an active practice session for periodic cloud upload."""
        now = time.time()
        if now - self._last_practice_persist < 15:
            return
        sessions = self.practice.snapshot().get("active_sessions") or []
        if not sessions:
            return
        self._last_practice_persist = now
        for session in sessions:
            if any((session.get("laps") or {}).values()):
                try:
                    self._persist_practice(session)
                except Exception:
                    # The next checkpoint retries. Storage problems must not
                    # suppress the incoming passing event or stop timing.
                    self._last_practice_persist = 0

    def _persist_practice(self, session: dict) -> None:
        """Save idle practice locally with stable driver/transponder identity."""
        # Closed recorder sessions already contain an immutable identity
        # snapshot. Keep it intact so later transponder reassignment does
        # not rename historical practice data. The fallback supports old
        # snapshots produced by pre-v2 recorder instances.
        drivers = list(session.get("drivers") or [])
        if not drivers:
            for transponder, laps in (session.get("laps") or {}).items():
                member = self.club.member_for_transponder(transponder) or {}
                drivers.append({
                    "transponder": int(transponder),
                    "driver_id": member.get("id"),
                    "driver": member.get("name") or f"TP {transponder}",
                    "nickname": member.get("nick") or "",
                    "class": member.get("kelas") or "",
                    "laps_ms": list(laps or []),
                    "best_ms": min(laps) if laps else None,
                })
        session = dict(session, drivers=drivers, schema_version=2,
                       identity_captured=bool(session.get("identity_captured", True)))
        try:
            self.club.simpan_practice(session)
            if self._cloud is not None:
                self._cloud.wake.set()
        except Exception as exc:
            self.engine.log("warning", f"Practice lokal gagal disimpan: {exc}")
            raise

    def tick(self) -> None:
        """Advance practice/race clocks even when no browser polls the API."""
        with self._club_lock:
            self._tick_locked()

    def _tick_locked(self) -> None:
        if self.race.status == "idle":
            active = next((t for t in self.club.tracks() if t.get("active")), None)
            if active:
                self.race.set_track(active["id"])
                self.cfg.track_name = active["name"]
            else:
                self.race.track_id = None
                self.cfg.track_name = ""
        self.practice.tick(race_status=self.race.status, track=self.race._track(),
                           idle_seconds=getattr(self.cfg, "idle_split_seconds", 120))
        self._maybe_persist_practice()

    @property
    def club(self):
        with self._club_lock:
            if self._club is None:
                from .club import ClubStore
                self._club = ClubStore(self.cloud.database_path())
            return self._club

    @property
    def race(self):
        with self._club_lock:
            if self._race is None:
                from .race import RaceEngine
                self._race = RaceEngine(self.club)
            return self._race

    @property
    def cloud(self):
        with self._club_lock:
            if self._cloud is None:
                from .cloud import CloudService
                from .config import default_data_dir
                self._cloud = CloudService(default_data_dir(), self.switch_database, self.can_refresh)
            return self._cloud

    def switch_database(self, path):
        from .club import ClubStore
        with self._club_lock:
            race = self._race
            if race:
                with race.lock:
                    if race.status != "idle":
                        raise ValueError("Simpan atau buang sesi race sebelum mengganti akun/club")
            # Flush practice sessions before replacing the store.  A recorder
            # session belongs to the selected club and must never leak into
            # the next account when the operator changes club.
            if self.practice:
                self.practice.close_all("club_changed")
                if self.practice.snapshot()["pending_persistence"]:
                    raise ValueError("Practice belum tersimpan. Coba lagi setelah penyimpanan pulih")
                from .practice import PracticeRecorder
                self.practice = PracticeRecorder(on_close=self._persist_practice,
                    idle_seconds=getattr(self.cfg, "idle_split_seconds", 120))
            if self._club:
                self._retired_stores.append(self._club)
            self._club = ClubStore(path)
            self._race = None

    def can_refresh(self, path):
        with self._club_lock:
            return not (self._club and self._club.path == path and self._race and self._race.status != "idle")


def make_handler(ctx: AppContext):
    cfg = ctx.cfg
    engine = ctx.engine

    class Handler(BaseHTTPRequestHandler):
        server_version = f"YurLaps/{APP_VERSION}"
        protocol_version = "HTTP/1.1"


        # -- util ---------------------------------------------------------
        def log_message(self, fmt, *args):  # senyap di konsol
            pass

        def _token_ok(self, qs: dict, allow_public: bool = False) -> bool:
            token = (cfg.access_token or "").strip()
            if not token or allow_public:
                return True
            supplied = (qs.get("t", [""])[0]
                        or self.headers.get("X-YurLaps-Token", "")
                        or self.headers.get("X-StintLab-Token", "")
                        or self.headers.get("Authorization", "").replace("Bearer ", "")
                        or self._cookie_token())
            return supplied == token

        def _cookie_token(self) -> str:
            """Token dari cookie sesi (dipasang lewat POST /api/session)."""
            raw = self.headers.get("Cookie", "") or ""
            for part in raw.split(";"):
                k, _, v = part.strip().partition("=")
                if k == "yurlaps_token":
                    return v
            return ""

        def _send(self, code: int, body: bytes, ctype: str,
                  extra: Optional[dict] = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Access-Control-Allow-Headers",
                                 "Content-Type, X-YurLaps-Token, X-StintLab-Token")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _err(self, code: int, msg: str) -> None:
            self._json({"ok": False, "error": msg}, code)

        def _body_json(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return {}
            if length <= 0 or length > 1_000_000:
                return {}
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                return {}

        def _origin_ok(self):
            origin = self.headers.get("Origin")
            return (not origin or origin == "http://" + self.headers.get("Host", "")) and \
                self.headers.get("Sec-Fetch-Site") != "cross-site"

        def _cloud_local(self):
            host = urlparse("http://" + self.headers.get("Host", "")).hostname
            return ipaddress.ip_address(self.client_address[0]).is_loopback and \
                host in ("localhost", "127.0.0.1", "::1") and self._origin_ok()

        # -- routing ------------------------------------------------------
        def do_OPTIONS(self):
            self._send(204, b"", "text/plain")

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            qs = parse_qs(parsed.query)

            if path.startswith("/api/") and not self._origin_ok():
                return self._err(403, "Origin tidak diizinkan")
            if path.startswith("/api/cloud/") and not self._cloud_local():
                return self._err(403, "Akun hanya dapat dikelola di komputer YurLaps")
            if path == "/api/health":
                return self._json({
                    "ok": True, "app": "YurLaps", "version": APP_VERSION,
                    "session": engine.session_name,
                    "track": cfg.track_name,
                    "decoder_online": engine.decoder_online,
                    "drivers": len(engine.drivers),
                    "started_at": ctx.start_time,
                })

            if path == "/api/sound/horn":
                from .config import default_data_dir
                horn = default_data_dir() / "sounds" / "custom-horn.mp3"
                if not horn.is_file():
                    return self._err(404, "Horn kustom belum dipasang")
                return self._send(200, horn.read_bytes(), "audio/mpeg",
                                  {"Cache-Control": "no-cache"})

            if path.startswith("/api/") and not self._token_ok(qs):
                return self._err(401, "Token akses salah. Buka URL dengan ?t=<token>.")

            if path == "/api/state":
                snap = engine.snapshot()
                snap["ok"] = True
                snap["stats"] = ctx.storage.stats() if ctx.storage else {}
                snap["online"] = {
                    "enabled": cfg.online_enabled,
                    "url": cfg.online_url,
                    "last_ok": getattr(ctx, "_online_last_ok", None),
                }
                try:
                    port = self.server.server_address[1]
                except Exception:
                    port = cfg.port
                snap["lan_urls"] = lan_urls(port, cfg.access_token)
                if ctx.decoder_manager is not None:
                    snap["decoder"]["manager"] = ctx.decoder_manager.status.to_dict()
                    snap["decoder"]["manager"]["available"] = (
                        ctx.decoder_manager.find_decoder() is not None)
                if ctx.updater is not None:
                    snap["update"] = ctx.updater.info()
                ctx.tick()
                snap["practice"] = ctx.practice.snapshot()
                # nilai yang dipakai halaman Decoder (jangan menebak di UI)
                names = {tp: m.get("nick") or m["name"] for m in ctx.club.members()
                         if m.get("status") == "active" for tp in m["transponder"]}
                snap["decoder"]["passes"] = [dict(p, driver=names.get(p["transponder_id"], p.get("driver", "")))
                                              for p in snap["decoder"]["passes"]]
                snap["decoder"]["gain"] = cfg.decoder_gain
                snap["decoder"]["name"] = "YurLaps Decoder"
                snap["decoder"]["selected_serial"] = cfg.decoder_device_serial
                snap["decoder"]["devices"] = [
                    dict(device, gain=(cfg.decoder_devices.get(device["serial"], {})
                                       .get("gain", cfg.decoder_gain)),
                         active=device["serial"] == cfg.decoder_device_serial and
                                bool(cfg.decoder_devices.get(device["serial"], {}).get("enabled")))
                    for device in snap["decoder"].get("device", {}).get("devices", [])]
                snap["settings"].update({
                    "track_name": cfg.track_name,
                    "event_name": cfg.event_name,
                    "zmq_endpoint": cfg.zmq_endpoint,
                    "port": cfg.port,
                })
                return self._json(snap)

            if path == "/api/device":
                from .devices import detect, can_install_driver, windows_driver_help
                info = detect(cfg.driver_vid, cfg.driver_pid)
                ok, why = can_install_driver()
                return self._json({
                    "ok": True, "device": info.to_dict(),
                    "decoder_args": cfg.effective_decoder_args(),
                    "decoder_gain": cfg.decoder_gain,
                    "can_install": ok, "install_reason": why,
                    "help": windows_driver_help(),
                    "platform": os.name,
                })

            if path == "/api/update":
                if ctx.updater is None:
                    return self._json({"ok": False, "error": "updater tidak aktif"})
                return self._json({"ok": True, "update": ctx.updater.info()})

            if path == "/api/cloud/status":
                from .cloud_entities import conflicts
                # Acquire context before store everywhere, including polling.
                # Re-entering ctx.club while holding its lock inverts tick's order.
                with ctx._club_lock:
                    store = ctx.club
                    with store.lock:
                        pending = conflicts(store)
                return self._json({"ok": True, "cloud": ctx.cloud.status(), "conflicts": pending})

            if path == "/api/sound/settings":
                keys = ("sound_passing", "sound_ignore", "sound_countdown",
                        "horn_enabled", "horn_file",
                        "announce_driver", "announce_position_change", "announce_best_lap",
                        "announce_position_mode", "announce_lap", "announce_best_lap_mode",
                        "announce_best_lap_every", "announce_last_lap", "announce_finish",
                        "tts_rate", "tts_voice", "tts_language", "beep_volume", "beep_duration_ms")
                return self._json({"ok": True, "settings": {k: getattr(cfg, k) for k in keys}})

            # ---------------- Quick Race (offline) ----------------
            if path == "/api/club":
                return self._json({"ok": True, "club": ctx.club.club(),
                                   "ringkasan": ctx.club.ringkasan()})

            if path == "/api/club/bootstrap":
                # satu panggilan untuk semua yang dibutuhkan UI
                return self._json({
                    "ok": True,
                    "club": ctx.club.club(),
                    "cloud": ctx.cloud.status(),
                    "tracks": ctx.club.tracks(),
                    "members": ctx.club.members(),
                    "presets": ctx.club.presets(),
                    "ringkasan": ctx.club.ringkasan(),
                    "race": ctx.race.snapshot(),
                    "practice": ctx.practice.snapshot(),
                })

            if path == "/api/tracks":
                return self._json({"ok": True, "tracks": ctx.club.tracks()})

            if path == "/api/members":
                return self._json({"ok": True, "members": ctx.club.members()})

            if path == "/api/guest/capture":
                result = ctx.guest_capture_result
                if result:
                    member = ctx.club.member_for_transponder(result["transponder"])
                    return self._json({"ok": True, "state": "captured", "capture": result,
                                       "profile": member})
                return self._json({"ok": True,
                                   "state": "waiting" if time.monotonic() < ctx.guest_capture_until else "idle"})

            if path == "/api/presets":
                return self._json({"ok": True, "presets": ctx.club.presets()})

            if path == "/api/race/state":
                ctx.race.cek_waktu()
                return self._json({"ok": True, "race": ctx.race.snapshot()})

            if path == "/api/practice/state":
                ctx.tick()
                ctx._maybe_persist_practice()
                return self._json({"ok": True, "practice": ctx.practice.snapshot()})

            if path == "/api/practice/history":
                return self._json({"ok": True, "sessions": ctx.club.practice_sessions()})

            if path == "/api/races":
                tid = (qs.get("track_id") or [None])[0]
                return self._json({"ok": True, "races": ctx.club.races(tid)})

            if path.startswith("/api/races/") and path.endswith("/pdf"):
                rid = path[len("/api/races/"):-4]
                race = ctx.club.race(rid)
                if not race:
                    return self._err(404, "Sesi tidak ditemukan")
                from .race_report import render_pdf
                return self._send(200, render_pdf(race), "application/pdf",
                                  {"Content-Disposition": 'attachment; filename="YurLaps-race.pdf"'})

            if path.startswith("/api/races/"):
                rid = path[len("/api/races/"):]
                r = ctx.club.race(rid)
                if not r:
                    return self._err(404, "Sesi tidak ditemukan")
                return self._json({"ok": True, "race": r})

            if path == "/api/sessions":
                return self._json({"ok": True,
                                   "sessions": ctx.storage.sessions() if ctx.storage else []})

            if path == "/api/mappings.json":
                data = {k: {
                    "transponder_id": v.transponder_id,
                    "transponder_type": v.transponder_type,
                    "driver": v.driver, "car": v.car,
                    "class_name": v.class_name, "note": v.note,
                } for k, v in cfg.mappings.items()}
                body = json.dumps({"app": "YurLaps", "mappings": data},
                                  indent=2, ensure_ascii=False).encode()
                return self._send(200, body, "application/json; charset=utf-8",
                                  {"Content-Disposition":
                                   'attachment; filename="stintlab-transponder.json"'})

            if path == "/api/drivers":
                return self._json({"ok": True,
                                   "drivers": ctx.storage.drivers() if ctx.storage else []})

            if path == "/api/session":
                want = (cfg.access_token or "").strip()
                return self._json({"ok": True, "required": bool(want)})

            if path == "/api/export.csv":
                if not ctx.storage:
                    return self._err(500, "Database tidak aktif")
                buf = io.StringIO()
                rows = ctx.storage.session_rows(engine.session_id)
                w = csv.writer(buf)
                w.writerow(["waktu", "transponder_id", "tipe", "pembalap", "stint",
                            "lap_ke", "waktu_lap_ms", "waktu_lap", "rssi", "mer",
                            "pass_duration_us"])
                for r in rows:
                    ms = int(r["time_ms"] or 0)
                    w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["at"])),
                                r["transponder_id"], r["transponder_type"] or "",
                                r["driver"] or "", r["stint_index"] or "", r["seq"] or "",
                                ms, f"{ms // 60000}:{(ms // 1000) % 60:02d}.{ms % 1000:03d}",
                                r["rssi"], r["mer"], r["pass_duration_us"]])
                name = f"stintlab-sesi{engine.session_id}.csv"
                return self._send(200, buf.getvalue().encode("utf-8-sig"),
                                  "text/csv; charset=utf-8",
                                  {"Content-Disposition": f'attachment; filename="{name}"'})

            if path == "/api/decoder/stats":
                # Diagnostik mentah dari protokol OpenStint (untuk panel Decoder)
                snap = engine.snapshot()["decoder"]
                snap["ok"] = True
                snap["decoder_binary"] = ctx.cfg.decoder_name
                if ctx.decoder_manager is not None:
                    snap["manager"] = ctx.decoder_manager.status.to_dict()
                    snap["manager"]["available"] = (
                        ctx.decoder_manager.find_decoder() is not None)
                return self._json(snap)

            if path == "/api/events":
                return self._stream(qs)

            # --- statis ---
            if path in ("/", "/index.html", "/quickrace", "/quickrace/"):
                # UI utama = Quick Race (Quick Race, History, Member, Track,
                # Decoder), sama dengan tangkapan layar di docs/app-qr-*.png.
                # Data dari API lokal, bukan localStorage.
                return self._serve_static("quickrace.html")
            if path in ("/practice", "/practice/", "/lama", "/lama/"):
                return self._serve_static("quickrace.html")
            if path.startswith("/static/"):
                return self._serve_static(path[len("/static/"):])
            if path == "/favicon.ico":
                # .ico asli (YurLaps) supaya tab browser & shortcut menampilkan logo
                if (STATIC_DIR / "assets" / "yurlaps.ico").is_file():
                    return self._serve_static("assets/yurlaps.ico")
                return self._serve_static("favicon.svg")
            if path == "/favicon.png":
                return self._serve_static("assets/favicon.png")
            return self._err(404, "Tidak ditemukan")

        def do_POST(self):
            parsed = urlparse(self.path)
            path = parsed.path
            qs = parse_qs(parsed.query)
            if path != "/api/session" and not self._token_ok(qs):
                return self._err(401, "Token akses salah")
            if not self._origin_ok():
                return self._err(403, "Origin tidak diizinkan")
            if path.startswith("/api/cloud/") and not self._cloud_local():
                return self._err(403, "Akun hanya dapat dikelola di komputer YurLaps")
            data = self._body_json()
            if not isinstance(data, dict):
                return self._err(400, "Badan permintaan harus objek JSON")

            if path == "/api/session":
                want = (cfg.access_token or "").strip()
                if not want:
                    return self._json({"ok": True, "required": False})
                if str(data.get("token") or "") != want:
                    return self._err(403, "Token salah")
                cookie = f"yurlaps_token={want}; Path=/; SameSite=Lax; Max-Age=604800"
                return self._send(200, b'{"ok":true}', "application/json; charset=utf-8",
                                  {"Set-Cookie": cookie})

            if path.startswith("/api/cloud/"):
                try:
                    if path == "/api/cloud/login":
                        return self._json({"ok": True, "url": ctx.cloud.begin_login()})
                    if path == "/api/cloud/password-login":
                        ctx.cloud.password_login(data.get("email"), data.get("password"))
                        return self._json({"ok": True, "cloud": ctx.cloud.status()})
                    if path == "/api/cloud/logout":
                        ctx.cloud.logout()
                    elif path == "/api/cloud/select":
                        ctx.cloud.select_club(data.get("club_id"))
                    elif path == "/api/cloud/sync":
                        ctx.cloud.wake.set()
                    elif path == "/api/cloud/resolve":
                        from .cloud_entities import resolve
                        resolve(ctx.club, data.get("kind"), data.get("id"), data.get("choice"))
                        ctx.cloud.wake.set()
                    else:
                        return self._err(404, "Tidak ditemukan")
                    return self._json({"ok": True, "cloud": ctx.cloud.status()})
                except (ValueError, KeyError, OSError, UploadError) as exc:
                    return self._err(409, str(exc))

            if path == "/api/sound/horn/upload":
                raw = data.get("data")
                if not isinstance(raw, str) or not raw:
                    return self._err(400, "File MP3 tidak ditemukan")
                try:
                    blob = base64.b64decode(raw, validate=True)
                except (ValueError, TypeError):
                    return self._err(400, "Data MP3 tidak valid")
                if len(blob) > 5 * 1024 * 1024:
                    return self._err(413, "Ukuran horn maksimal 5 MB")
                if not (blob.startswith(b"ID3") or (len(blob) >= 2 and blob[0] == 0xff and blob[1] & 0xe0 == 0xe0)):
                    return self._err(400, "File harus berformat MP3")
                from .config import default_data_dir
                target = default_data_dir() / "sounds" / "custom-horn.mp3"
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_suffix(".tmp")
                tmp.write_bytes(blob)
                tmp.replace(target)
                cfg.horn_file = "custom"
                cfg.horn_enabled = True
                cfg.save()
                return self._json({"ok": True, "file": "custom", "url": "/api/sound/horn"})

            # ---------------- Quick Race (offline) ----------------
            if path == "/api/members/import":
                try:
                    count = ctx.club.import_members(data.get("members"))
                except (ValueError, TypeError) as exc:
                    return self._err(400, str(exc))
                return self._json({"ok": True, "count": count, "members": ctx.club.members()})

            if path == "/api/member":
                try:
                    member = ctx.club.save_member(data)
                except (ValueError, TypeError) as exc:
                    return self._err(400, str(exc))
                return self._json({"ok": True, "member": member, "members": ctx.club.members()})

            if path == "/api/member/delete":
                member_id = str(data.get("id") or "")
                if not ctx.club.delete_member(member_id):
                    return self._err(403, "Member online dikelola dari website dan tidak dapat dihapus di aplikasi")
                return self._json({"ok": True, "members": ctx.club.members()})

            if path == "/api/guest/capture/start":
                if ctx.race.status != "idle":
                    return self._err(409, "Tangkap transponder hanya tersedia saat tidak ada race")
                with ctx._club_lock:
                    ctx.guest_capture_result = None
                    ctx.guest_capture_until = time.monotonic() + 30.0
                return self._json({"ok": True, "state": "waiting"})

            if path == "/api/guest/save":
                captured = ctx.guest_capture_result
                if not captured or str(captured["transponder"]) != str(data.get("transponder")):
                    return self._err(409, "Tangkap transponder terlebih dahulu")
                try:
                    guest = ctx.club.save_local_guest(captured["transponder"], data.get("name"),
                                                      data.get("tts_name"), data.get("nick", ""),
                                                      data.get("kelas", ""), data.get("mobil", ""))
                except (ValueError, TypeError) as exc:
                    return self._err(400, str(exc))
                ctx.guest_capture_result = None
                return self._json({"ok": True, "guest": guest, "members": ctx.club.members()})

            if path == "/api/guest/resolve":
                if ctx.race.status != "idle":
                    return self._err(409, "Selesaikan race sebelum mengubah identitas tamu")
                try:
                    ctx.club.resolve_local_guest(data.get("transponder"), str(data.get("account_id") or ""))
                except (ValueError, TypeError) as exc:
                    return self._err(400, str(exc))
                return self._json({"ok": True, "members": ctx.club.members()})

            if path == "/api/race/track":
                try:
                    ctx.race.set_track(data.get("track_id") or "")
                    track = ctx.club.track(ctx.race.track_id)
                    ctx.club.save_track(dict(track, active=True))
                    ctx.tick()
                except ValueError as e:
                    return self._err(400, str(e))
                return self._json({"ok": True, "race": ctx.race.snapshot()})

            if path == "/api/race/cfg":
                try:
                    ctx.race.set_cfg(data)
                except (ValueError, TypeError) as exc:
                    return self._err(409, str(exc))
                return self._json({"ok": True, "race": ctx.race.snapshot()})

            if path == "/api/race/peserta":
                try:
                    ctx.race.set_peserta(data.get("peserta") or [])
                except ValueError as e:
                    return self._err(400, str(e))
                return self._json({"ok": True, "race": ctx.race.snapshot()})

            if path == "/api/race/start":
                try:
                    with ctx._club_lock:
                        if ctx.update_pending:
                            return self._err(409, "Pembaruan sedang dipasang")
                        snap = ctx.race.mulai()
                        ctx.tick()
                except ValueError as e:
                    return self._err(400, str(e))
                return self._json({"ok": True, "race": snap})

            if path == "/api/race/go":
                try:
                    snap = ctx.race.go()
                except ValueError as e:
                    return self._err(400, str(e))
                return self._json({"ok": True, "race": snap})

            if path == "/api/race/resume":
                try:
                    snap = ctx.race.resume()
                except ValueError as e:
                    return self._err(409, str(e))
                return self._json({"ok": True, "race": snap})

            if path == "/api/race/pass":
                return self._err(409, "Pencatatan race hanya menerima lintasan OpenStint.")

            if path == "/api/race/finish":
                try:
                    snap = ctx.race.selesai(data.get("alasan") or "dihentikan operator")
                    ctx.tick()
                except ValueError as e:
                    return self._err(400, str(e))
                return self._json({"ok": True, "race": snap})

            if path == "/api/race/reset":
                try:
                    snap = ctx.race.reset()
                except ValueError as exc:
                    return self._err(409, str(exc))
                ctx.tick()
                return self._json({"ok": True, "race": snap})

            if path == "/api/practice/visibility":
                return self._err(409, "Privasi practice diatur dari dashboard website pemilik akun")

            if path == "/api/race/save":
                try:
                    info = ctx.race.simpan(data.get("nama"))
                    ctx.tick()
                    ctx.cloud.wake.set()
                except ValueError as e:
                    return self._err(400, str(e))
                return self._json({"ok": True, "tersimpan": info,
                                   "ringkasan": ctx.club.ringkasan()})

            if path == "/api/race/discard":
                try:
                    ctx.race.buang()
                except ValueError as exc:
                    return self._err(409, str(exc))
                return self._json({"ok": True, "race": ctx.race.snapshot()})

            if path == "/api/race/log/clear":
                with ctx._club_lock:
                    race = ctx.race
                    with race.lock:
                        race.log.clear()
                return self._json({"ok": True, "race": ctx.race.snapshot()})

            if path == "/api/races/delete":
                rid = str(data.get("id") or "")
                if not rid:
                    return self._err(400, "id sesi wajib")
                ok = ctx.club.hapus_race(rid)
                if not ok:
                    return self._err(404, "Sesi tidak ditemukan")
                if getattr(ctx, "cloud", None) is not None:
                    ctx.cloud.wake.set()
                return self._json({"ok": True, "deleted": rid})

            if path == "/api/races/import":
                if not self._cloud_local():
                    return self._err(403, "Impor hanya dari komputer YurLaps")
                try:
                    from .race_import import import_lapbeeps
                    with ctx._club_lock:
                        if ctx.race.status != "idle":
                            raise ValueError("Selesaikan dan reset race sebelum impor")
                        track = next((t for t in ctx.club.tracks() if t.get("active")), None)
                        if not track:
                            raise ValueError("Aktifkan track tujuan di menu Track")
                        result = import_lapbeeps(ctx.club, data.get("document"), track["id"])
                    ctx.cloud.wake.set()
                    return self._json({"ok": True, **result})
                except (ValueError, KeyError, TypeError) as exc:
                    return self._err(400, str(exc))

            if path == "/api/race/upload":
                rid = data.get("id") or ""
                race = ctx.club.race(rid)
                if not race:
                    return self._err(404, "Sesi tidak ditemukan")
                if not race.get("club_id"):
                    return self._err(409, "Sesi lokal lama belum terikat club; tidak diunggah otomatis")
                if not getattr(ctx, "cloud", None) or not ctx.cloud.status().get("user_id"):
                    return self._err(409, "Login akun leader agar antrean dapat diunggah")
                ctx.cloud.wake.set()
                return self._json({"ok": True, "queued": True})

            if path == "/api/track":
                try:
                    if ctx.race.status != "idle":
                        return self._err(409, "Simpan atau buang sesi lalu Reset sebelum mengubah track")
                    t = ctx.club.save_track(data)
                    ctx.tick()
                except ValueError as e:
                    return self._err(400, str(e))
                return self._json({"ok": True, "track": t, "tracks": ctx.club.tracks()})

            if path == "/api/track/delete":
                try:
                    if ctx.race.status != "idle":
                        return self._err(409, "Simpan atau buang sesi lalu Reset sebelum menghapus track")
                    ok = ctx.club.delete_track(data.get("id") or "")
                    ctx.tick()
                except ValueError as e:
                    return self._err(400, str(e))
                return self._json({"ok": ok, "tracks": ctx.club.tracks()})

            if path == "/api/tracks/sync":
                if not getattr(ctx, "cloud", None) or not ctx.cloud.status().get("user_id"):
                    return self._err(409, "Login akun leader untuk sinkronisasi; data tetap tersimpan lokal")
                ctx.cloud.wake.set()
                return self._json({"ok": True, "queued": True})

            if path == "/api/preset":
                try:
                    pr = ctx.club.save_preset(data)
                except ValueError as e:
                    return self._err(400, str(e))
                return self._json({"ok": True, "preset": pr,
                                   "presets": ctx.club.presets()})

            if path == "/api/preset/delete":
                try:
                    ok = ctx.club.delete_preset(data.get("id") or "")
                except ValueError as e:
                    return self._err(400, str(e))
                return self._json({"ok": ok, "presets": ctx.club.presets()})

            if path == "/api/driver":
                tid = data.get("transponder_id")
                if tid is None:
                    return self._err(400, "transponder_id wajib")
                engine.update_driver(int(tid),
                                     driver=data.get("driver"),
                                     car=data.get("car"),
                                     class_name=data.get("class_name"),
                                     note=data.get("note"))
                return self._json({"ok": True, "state": engine.snapshot()})

            if path == "/api/driver/delete":
                try:
                    engine.remove_driver(int(data["transponder_id"]))
                except (KeyError, TypeError, ValueError):
                    return self._err(400, "transponder_id harus angka")
                return self._json({"ok": True, "state": engine.snapshot()})

            if path == "/api/session/new":
                engine.new_session(data.get("name", ""))
                return self._json({"ok": True, "state": engine.snapshot()})

            if path == "/api/stint/close":
                tid = data.get("transponder_id")
                if tid is None:
                    return self._err(400, "transponder_id wajib")
                engine.close_stint(int(tid), reason="manual")
                return self._json({"ok": True, "state": engine.snapshot()})

            if path == "/api/settings":
                if ctx.race.status in ("live", "countdown", "paused") and any(k.startswith("decoder_") or k in ("manage_decoder", "zmq_endpoint") for k in data):
                    return self._err(409, "Pengaturan decoder terkunci selama sesi berjalan")
                allowed = {
                    "track_name": str, "event_name": str, "zmq_endpoint": str,
                    "idle_split_seconds": int, "min_lap_seconds": float,
                    "beep_on_lap": bool, "announce_lap_times": bool,
                    "beep_volume": float, "access_token": str,
                    "sound_passing": bool, "sound_ignore": bool, "sound_countdown": bool,
                    "horn_enabled": bool, "horn_file": str,
                    "announce_driver": bool, "announce_position_change": bool,
                    "announce_position_mode": str, "announce_lap": bool,
                    "announce_best_lap_mode": str,
                    "announce_best_lap": bool, "announce_best_lap_every": int,
                    "announce_last_lap": bool, "announce_finish": bool,
                    "tts_rate": float, "tts_voice": str, "tts_language": str, "beep_duration_ms": int,
                    "manage_decoder": bool, "decoder_exe": str,
                    "decoder_args": str, "decoder_auto_download": bool,
                    "decoder_gain": float, "decoder_bias_tee": bool,
                    "decoder_direct_sampling": bool, "decoder_device_serial": str,
                    "decoder_use_system_clock": bool, "decoder_monitor": bool,
                    "decoder_restart_max_delay": int, "decoder_idle_restart": int,
                    "driver_vid": str, "driver_pid": str,
                    "auto_check_updates": bool, "update_manifest_url": str,
                    "online_enabled": bool, "online_mode": str,
                    "online_url": str,
                    "online_token": str, "sync_interval": float,
                }
                import math
                updates = {}
                ranges = {"idle_split_seconds": (1, 86400), "beep_volume": (0, 1), "beep_duration_ms": (20, 300),
                          "tts_rate": (.5, 2), "announce_best_lap_every": (1, 10000),
                          "decoder_gain": (0, 49.6)}
                modes = {"announce_position_mode": ("off", "passing", "changed"),
                         "announce_best_lap_mode": ("off", "interval", "immediate")}
                try:
                    for key, typ in allowed.items():
                        if key not in data:
                            continue
                        value = data[key]
                        if typ is bool:
                            if not isinstance(value, bool):
                                raise ValueError(key + " harus true/false")
                        else:
                            value = typ(value)
                        if typ in (int, float) and not math.isfinite(value):
                            raise ValueError(key + " harus angka valid")
                        if key in ranges and not ranges[key][0] <= value <= ranges[key][1]:
                            raise ValueError(key + " di luar batas yang diizinkan")
                        if key in modes and value not in modes[key]:
                            raise ValueError(key + " tidak valid")
                        updates[key] = value
                except (TypeError, ValueError) as exc:
                    return self._err(400, str(exc))
                for key, value in updates.items():
                    setattr(cfg, key, value)
                cfg.save()
                # gain / opsi radio berubah lewat form setelan -> restart decoder
                restart = False
                gain_keys = ("decoder_gain", "decoder_bias_tee",
                             "decoder_direct_sampling", "decoder_device_serial",
                             "decoder_use_system_clock", "decoder_monitor",
                             "decoder_args")
                if any(k in data for k in gain_keys) and ctx.decoder_manager is not None:
                    if ctx.cfg.manage_decoder and ctx.decoder_manager.status.running:
                        ctx.decoder_manager.restart_later(
                            1.5, reason=f"gain {ctx.cfg.decoder_gain:g} dB")
                        restart = True
                engine.log("info", "Setelan disimpan")
                return self._json({"ok": True, "restarting_decoder": restart,
                                   "state": engine.snapshot()})

            if path == "/api/mappings/import":
                items = data.get("mappings") or {}
                if isinstance(items, list):
                    items = {str(i.get("transponder_id")): i for i in items
                             if isinstance(i, dict) and i.get("transponder_id") is not None}
                count = 0
                for key, val in items.items():
                    if not isinstance(val, dict):
                        continue
                    try:
                        tid = int(val.get("transponder_id", key))
                    except (TypeError, ValueError):
                        continue
                    engine.update_driver(
                        tid,
                        driver=val.get("driver", ""),
                        car=val.get("car", ""),
                        class_name=val.get("class_name", ""),
                        note=val.get("note", ""),
                    )
                    count += 1
                engine.log("info", f"{count} data transponder diimpor")
                return self._json({"ok": True, "imported": count,
                                   "state": engine.snapshot()})

            if path == "/api/device/install-driver":
                from .devices import install_driver
                res = install_driver(cfg.driver_vid, cfg.driver_pid)
                engine.log("ok" if res.ok else "error", res.message)
                return self._json({"ok": res.ok, "message": res.message,
                                   "log": res.log[-4000:],
                                   "needs_admin": res.needs_admin})

            if path == "/api/device/redetect":
                from .devices import detect
                info = detect(cfg.driver_vid, cfg.driver_pid)
                engine.device_info = info.to_dict()
                engine.log("info", f"Perangkat: {info.message}")
                return self._json({"ok": True, "device": info.to_dict()})

            if path in ("/api/decoder/gain", "/api/decoder/restart", "/api/decoder/select") and ctx.race.status in ("live", "countdown", "paused"):
                return self._err(409, "Selesaikan sesi sebelum mengubah gain atau restart decoder.")

            if path == "/api/decoder/select":
                serial = str(data.get("serial") or "").strip()
                enabled = data.get("enabled")
                if not serial or not isinstance(enabled, bool):
                    return self._err(400, "Pilih serial dan status aktivasi yang valid.")
                from .devices import list_sdr_devices
                devices = list_sdr_devices(cfg.driver_vid, cfg.driver_pid)
                found = next((d for d in devices if d["serial"] == serial), None)
                if enabled and (not found or not found["selectable"]):
                    return self._err(400, "Decoder ID belum terbaca. Periksa perangkat dan driver USB.")
                if enabled:
                    try:
                        gain = float(data.get("gain", cfg.decoder_devices.get(serial, {}).get("gain", cfg.decoder_gain)))
                    except (TypeError, ValueError):
                        return self._err(400, "Gain harus berupa angka.")
                    import math
                    if not math.isfinite(gain) or not 0 <= gain <= 49.6:
                        return self._err(400, "Gain harus 0–49,6 dB.")
                    cfg.decoder_devices[serial] = {"enabled": True, "gain": gain}
                    cfg.decoder_device_serial = serial
                    cfg.decoder_gain = gain
                    cfg.manage_decoder = True
                else:
                    cfg.decoder_devices.setdefault(serial, {"gain": cfg.decoder_gain})["enabled"] = False
                    if cfg.decoder_device_serial == serial:
                        cfg.decoder_device_serial = ""
                cfg.save()
                if ctx.decoder_manager is not None:
                    ctx.decoder_manager.device_changed(bool(enabled))
                    if enabled and not ctx.decoder_manager.is_alive() and not ctx.decoder_manager._stop_event.is_set():
                        ctx.decoder_manager.start()
                    elif ctx.decoder_manager.is_alive():
                        ctx.decoder_manager.restart_now(reason="pilihan serial diubah")
                return self._json({"ok": True, "serial": serial, "enabled": enabled,
                                   "gain": cfg.decoder_gain})

            if path == "/api/decoder/gain":
                # Ubah gain tuner -> simpan -> restart openstint.exe (debounce).
                try:
                    gain = float(data.get("gain"))
                except (TypeError, ValueError):
                    return self._err(400, "gain harus berupa angka (0..49.6 dB)")
                mgr = ctx.decoder_manager
                if mgr is None:
                    ctx.cfg.decoder_gain = gain
                    ctx.cfg.save()
                    return self._json({"ok": True, "gain": gain,
                                       "state": engine.snapshot(),
                                       "message": "Gain disimpan (supervisor "
                                                  "decoder tidak aktif)."})
                applied = mgr.apply_gain(gain)
                serial = cfg.decoder_device_serial
                if serial:
                    cfg.decoder_devices.setdefault(serial, {"enabled": True})["gain"] = applied
                    cfg.save()
                engine.log("info", f"Gain decoder diubah ke {applied:g} dB")
                restarted = bool(mgr.status.running and ctx.cfg.manage_decoder)
                return self._json({
                    "ok": True, "gain": applied, "restarted": restarted,
                    "state": engine.snapshot(),
                    "message": (f"Gain {applied:g} dB disimpan. "
                                "openstint.exe dijalankan ulang otomatis."
                                if restarted else
                                f"Gain {applied:g} dB disimpan. Decoder tidak "
                                "sedang dikelola YurLaps, jadi tidak di-restart."),
                })

            if path == "/api/decoder/restart":
                if ctx.decoder_manager is None:
                    return self._json({"ok": False, "error": "manajer decoder tidak aktif"})
                ctx.decoder_manager.restart_now()
                return self._json({"ok": True,
                                   "manager": ctx.decoder_manager.status.to_dict()})

            if path == "/api/decoder/probe":
                # coba buka dongle sungguhan: jalankan decoder 12 detik, lihat log
                if ctx.decoder_manager is None:
                    return self._json({"ok": False, "error": "manajer decoder tidak aktif"})
                probe = ctx.decoder_manager.probe(timeout=12)
                return self._json(probe)

            if path in ("/api/update/prepare", "/api/update/cancel"):
                if not self._cloud_local():
                    return self._err(403, "Pembaruan hanya dari komputer ini")
                with ctx._club_lock, ctx.race.lock:
                    if path.endswith("cancel"):
                        ctx.update_pending = False
                    else:
                        if ctx.race.status != "idle":
                            return self._err(409, "Selesaikan dan simpan race sebelum memasang pembaruan")
                        ctx.update_pending = True
                return self._json({"ok": True})

            if path == "/api/update/check":
                if ctx.updater is None:
                    return self._json({"ok": False, "error": "updater tidak aktif"})
                info = ctx.updater.check()
                return self._json({"ok": True, "update": info})

            if path == "/api/update/download":
                if ctx.updater is None:
                    return self._json({"ok": False, "error": "updater tidak aktif"})
                ctx.updater.download_async()
                return self._json({"ok": True, "update": ctx.updater.info()})

            if path == "/api/update/apply":
                if ctx.updater is None:
                    return self._json({"ok": False, "error": "updater tidak aktif"})
                if os.environ.get("YURLAPS_ELECTRON") == "1":
                    return self._err(409, "Gunakan menu Pembaruan aplikasi Electron")
                if ctx.race.status != "idle":
                    return self._err(409, "Simpan race sebelum memasang pembaruan")
                ok, msg = ctx.updater.apply_and_restart()
                return self._json({"ok": ok, "message": msg})

            if path == "/api/push-online":
                from .online import OnlineSync
                sync = OnlineSync(engine)
                ok = sync.push_once()
                return self._json({"ok": ok, "error": sync.last_error})

            return self._err(404, "Tidak ditemukan")

        # -- SSE ----------------------------------------------------------
        def _stream(self, qs: dict) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()

            q = ctx.bus.subscribe()
            try:
                hello = {"kind": "hello", "state": engine.snapshot()}
                self.wfile.write(_sse(hello))
                self.wfile.flush()
                last_ping = time.time()
                while True:
                    try:
                        ev = q.get(timeout=1.0)
                        self.wfile.write(_sse(ev))
                        self.wfile.flush()
                    except queue.Empty:
                        now = time.time()
                        if now - last_ping > 15:
                            last_ping = now
                            self.wfile.write(b": ping\n\n")
                            self.wfile.flush()
                    if ctx.bus is None:
                        break
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                ctx.bus.unsubscribe(q)

        # -- statis -------------------------------------------------------
        def _serve_static(self, name: str) -> None:
            name = name.split("?")[0].lstrip("/")
            target = (STATIC_DIR / name).resolve()
            try:
                target.relative_to(STATIC_DIR.resolve())
            except ValueError:
                return self._err(403, "Akses ditolak")
            if not target.is_file():
                return self._err(404, "Tidak ditemukan")
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript",):
                ctype += "; charset=utf-8"
            body = target.read_bytes()
            extra = {"Cache-Control": "no-cache"}
            return self._send(200, body, ctype, extra)

    return Handler


def _sse(obj: dict) -> bytes:
    try:
        payload = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        payload = '{"kind":"error"}'
    return f"data: {payload}\n\n".encode("utf-8")


def start_server(ctx: AppContext, host: str, port: int,
                 block: bool = True) -> ThreadingHTTPServer:
    handler = make_handler(ctx)
    ThreadingHTTPServer.allow_reuse_address = True
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    if block:
        httpd.serve_forever()
    else:
        t = threading.Thread(target=httpd.serve_forever, name="http", daemon=True)
        t.start()
    return httpd
