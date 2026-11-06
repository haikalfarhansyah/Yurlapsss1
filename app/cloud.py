"""Supabase account, club caches and persistent offline sync for the desktop app.

No network operation is called from the decoder or race timing path. Every
club/account has its own SQLite file. Only validated active leaders can sync.
"""
from __future__ import annotations

import html
import ipaddress
import json
import os
import secrets
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .club import ClubStore
from .cloud_credentials import CredentialStore
from .cloud_entities import sync_entities, conflicts
from .member_sync import _read_all, refresh_members
from .race_upload import SupabaseRaceClient, UploadError, upload_pending, upload_pending_practice

DEFAULT_URL = "https://eottbwdcxrhgxadwnlmy.supabase.co"
# Publishable client key only; authorization is enforced by the user's JWT + RLS.
DEFAULT_KEY = "sb_publishable_pNLXpdEMYvhqyvDvfFLS_g_CfAYv1rX"
DEFAULT_WEBSITE = "https://yurlaps.dpdns.org"


class CloudService:
    def __init__(self, data_dir, on_switch=None, can_refresh=None, client_factory=SupabaseRaceClient):
        self.root = Path(data_dir)
        self.credentials = CredentialStore(self.root / "cloud-session.json")
        self.state = self.credentials.load()
        self.url = os.environ.get("YURLAPS_SUPABASE_URL", DEFAULT_URL).rstrip("/")
        self.key = os.environ.get("YURLAPS_SUPABASE_KEY", DEFAULT_KEY)
        self.website = os.environ.get("YURLAPS_WEBSITE_URL", DEFAULT_WEBSITE).rstrip("/")
        if not self.website.startswith("https://"):
            raise ValueError("Website login harus HTTPS")
        # Never replay a persisted credential against a newly configured project.
        if self.state.get("project_url") != self.url:
            self.state = {}
        self.client_factory = client_factory
        self.on_switch = on_switch or (lambda path: None)
        self.can_refresh = can_refresh or (lambda path: True)
        self.operation = threading.RLock()
        self.state_lock = threading.RLock()
        self.wake = threading.Event()
        self.stopped = threading.Event()
        self.thread = None
        self.last_ok = None
        self.last_error = None
        self.syncing = False
        self.login_server = None
        self.login_state = None
        self.login_deadline = 0

    def cache_path(self, user_id, club_id):
        return self.root / "clubs" / str(uuid.UUID(user_id)) / (str(uuid.UUID(club_id)) + ".sqlite3")

    def database_path(self):
        with self.state_lock:
            if self.state.get("user_id") and self.state.get("selected_club"):
                return self.cache_path(self.state["user_id"], self.state["selected_club"])
        return self.root / "yurlaps_club.sqlite3"

    def _save(self):
        self.credentials.save(self.state)

    def _client(self):
        if not self.state.get("refresh_token"):
            raise UploadError("Login melalui website untuk mengaktifkan sinkronisasi", 401)
        client = self.client_factory(self.url, self.key, self.state["access_token"])
        if self.state.get("expires_at", 0) < time.time() + 90:
            try:
                session = client._request("/auth/v1/token?grant_type=refresh_token", "POST",
                                          {"refresh_token": self.state["refresh_token"]})
            except UploadError as exc:
                if exc.status in (400, 401, 403):
                    with self.state_lock:
                        self.state.pop("access_token", None)
                        self.state.pop("refresh_token", None)
                        self._save()
                    raise UploadError("Sesi berakhir. Login lagi; data offline tetap tersimpan", 401) from exc
                raise
            if session.get("user", {}).get("id") != self.state["user_id"]:
                raise UploadError("Akun sesi tidak cocok", 401)
            with self.state_lock:
                self.state.update(access_token=session["access_token"], refresh_token=session["refresh_token"],
                                  expires_at=time.time() + session.get("expires_in", 3600))
                self._save()  # persist rotated refresh token before making any other request
            client = self.client_factory(self.url, self.key, self.state["access_token"])
        return client

    @staticmethod
    def leader_clubs(client, user_id):
        rows = _read_all(client, "club_members", {"select": "club_id,role,status", "user_id": "eq." + user_id,
                         "role": "eq.leader", "status": "eq.active", "order": "club_id.asc"})
        ids = [str(uuid.UUID(r["club_id"])) for r in rows if r.get("role") == "leader" and r.get("status") == "active"]
        clubs = []
        for start in range(0, len(ids), 50):
            clubs.extend(_read_all(client, "clubs", {"select": "id,name,description,address,role_permissions",
                         "id": "in.(" + ",".join(ids[start:start+50]) + ")", "order": "id.asc"}))
        return [dict(c, role="leader") for c in clubs if c["id"] in ids]

    def accept_session(self, session):
        with self.operation:
            client = self.client_factory(self.url, self.key, session["access_token"])
            user = client._request("/auth/v1/user")
            uid = str(uuid.UUID(user["id"]))
            if user.get("is_anonymous"):
                raise ValueError("Gunakan akun website YurLaps")
            clubs = self.leader_clubs(client, uid)
            if not clubs:
                raise ValueError("Akun ini belum menjadi leader aktif pada club mana pun")
            previous = self.state.get("selected_club") if self.state.get("user_id") == uid else None
            selected = next((c for c in clubs if c["id"] == previous), clubs[0])
            path = self.cache_path(uid, selected["id"])
            fresh = not path.exists()
            store = ClubStore(path)
            try:
                if fresh:
                    with store._conn:
                        store._conn.execute("DELETE FROM track WHERE id='t-local'")
                store.set_club(club_id=selected["id"], name=selected["name"], role="leader")
                if fresh:
                    self._migrate_unassigned_local_history(store, selected["id"])
            finally:
                store.close()
            self.on_switch(path)  # can reject if a race is still running/unsaved
            with self.state_lock:
                self.state = {"project_url": self.url, "user_id": uid, "email": user.get("email", ""),
                              "access_token": session["access_token"], "refresh_token": session["refresh_token"],
                              "expires_at": float(session.get("expires_at") or time.time() + 300),
                              "selected_club": selected["id"], "clubs": clubs}
                self._save()
            self.last_error = None
            self.wake.set()

    def _migrate_unassigned_local_history(self, target: ClubStore, club_id: str) -> None:
        """Attach offline results made before login to the selected club.

        A logged-out computer cannot know the remote club UUID. Once the user
        explicitly signs in and selects a club, unassigned local history is
        copied into that club cache and enters the normal upload queue.
        """
        local_path = self.root / "yurlaps_club.sqlite3"
        if not local_path.exists() or local_path.resolve() == target.path.resolve():
            return
        local = ClubStore(local_path)
        try:
            for row in local._conn.execute(
                "SELECT id FROM race WHERE club_id IS NULL OR club_id = '' OR club_id = 'club-local'"
            ).fetchall():
                race = local.race(row["id"])
                if race:
                    target.simpan_race(dict(race, club_id=club_id, sync="belum"))
            for session in local.practice_sessions(1000):
                if session.get("club_id") in (None, "", "club-local"):
                    target.simpan_practice(session, club_id=club_id)
        finally:
            local.close()

    def select_club(self, club_id):
        with self.operation:
            club = next((c for c in self.state.get("clubs", []) if c["id"] == club_id), None)
            if not club:
                raise ValueError("Club tidak tersedia untuk akun ini")
            path = self.cache_path(self.state["user_id"], club_id)
            fresh = not path.exists()
            store = ClubStore(path)
            try:
                if fresh:
                    with store._conn:
                        store._conn.execute("DELETE FROM track WHERE id='t-local'")
                store.set_club(club_id=club_id, name=club["name"], role="leader")
            finally:
                store.close()
            self.on_switch(path)
            with self.state_lock:
                self.state["selected_club"] = club_id
                self._save()
            self.wake.set()

    def logout(self):
        with self.operation:
            old = dict(self.state)
            self.on_switch(self.root / "yurlaps_club.sqlite3")
            with self.state_lock:
                self.state = {}
                self._save()
            self.last_ok = self.last_error = None
        if old.get("access_token"):
            def revoke():
                try:
                    self.client_factory(self.url, self.key, old["access_token"])._request(
                        "/auth/v1/logout?scope=local", "POST")
                except UploadError:
                    pass
            threading.Thread(target=revoke, daemon=True).start()

    def status(self):
        with self.state_lock:
            state = {k: self.state.get(k) for k in ("user_id", "email", "selected_club", "clubs")}
            state["login_required"] = bool(self.state.get("user_id") and not self.state.get("refresh_token"))
        state.update(syncing=self.syncing, last_ok=self.last_ok, error=self.last_error,
                     online=bool(self.last_ok and not self.last_error and time.time()-self.last_ok < 90),
                     website=self.website)
        return state  # tokens never leave the Python process

    def sync_once(self):
        with self.operation:
            if not self.state.get("refresh_token"):
                return
            self.syncing = True
            errors = []
            try:
                client = self._client()
                clubs = self.leader_clubs(client, self.state["user_id"])
                with self.state_lock:
                    self.state["clubs"] = clubs
                    self._save()
                if self.state.get("selected_club") not in {c["id"] for c in clubs}:
                    errors.append("Akses leader club aktif dicabut. Data lokal disimpan; unggah dihentikan")
                for club in clubs:
                    path = self.cache_path(self.state["user_id"], club["id"])
                    if not path.exists():
                        continue
                    store = ClubStore(path)
                    try:
                        # Upload independently: a metadata conflict never blocks race results.
                        upload_pending(store, club["id"], self.state["user_id"], client)
                        upload_pending_practice(store, club["id"], self.state["user_id"], client)
                        if self.can_refresh(path):
                            refresh_members(store, club["id"], client)
                            sync_entities(store, club["id"], client)
                            store.set_club(name=club["name"], role="leader", synced_at=time.time())
                    except Exception as exc:
                        errors.append(str(exc))
                    finally:
                        store.close()
                self.last_error = "; ".join(errors) or None
                if not errors:
                    self.last_ok = time.time()
            except Exception as exc:
                self.last_error = str(exc)
            finally:
                self.syncing = False

    def start(self):
        if self.thread:
            return
        self.thread = threading.Thread(target=self._run, name="club-sync", daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stopped.is_set():
            self.wake.clear()
            self.sync_once()
            # Practice checkpoints are uploaded promptly while online.
            self.wake.wait(15)

    def stop(self):
        self.stopped.set()
        self.wake.set()
        if self.login_server:
            self.login_server.shutdown()
            self.login_server.server_close()

    def begin_login(self):
        """One-use, five-minute login receiver bound only to loopback.

        The website POSTs an independent desktop session, never a token in a
        URL. The receiver validates Origin + Host + cryptographic state.
        """
        if self.login_server:
            self.login_server.shutdown()
            self.login_server.server_close()
        self.login_state = secrets.token_urlsafe(32)
        self.login_deadline = time.time() + 300
        service = self
        expected_state = self.login_state

        class Callback(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                code, message = 400, "Permintaan login tidak valid. Ulangi dari aplikasi YurLaps."
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not (0 < length < 65536) or self.path != "/callback":
                        raise ValueError(message)
                    if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                        raise ValueError(message)
                    if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                        raise ValueError(message)
                    origin = self.headers.get("Origin")
                    # Chromium taints Origin on an HTTPS -> loopback HTTP form POST.
                    # Allow that browser navigation only; state remains mandatory.
                    opaque_navigation = (origin == "null"
                        and self.headers.get("Sec-Fetch-Mode") == "navigate"
                        and self.headers.get("Sec-Fetch-Dest") == "document"
                        and self.headers.get("Content-Type", "").split(";")[0] == "application/x-www-form-urlencoded")
                    if origin != service.website and not opaque_navigation:
                        raise ValueError(message)
                    data = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                    supplied = data.get("state", [""])[0]
                    with service.operation:
                        if time.time() > service.login_deadline or service.login_state != expected_state \
                                or not secrets.compare_digest(supplied, expected_state):
                            raise ValueError("Permintaan kedaluwarsa. Ulangi login dari aplikasi.")
                        service.login_state = None
                        service.accept_session(json.loads(data.get("session", ["{}"]) [0]))
                    code, message = 200, "Login berhasil. Kembali ke aplikasi YurLaps; tab ini dapat ditutup."
                except Exception as exc:
                    service.last_error = str(exc)
                    message = str(exc)
                body = ("<!doctype html><meta charset=utf-8><title>YurLaps</title>"
                        "<meta name=referrer content=no-referrer><h1>YurLaps</h1><p>" + html.escape(message) + "</p>").encode()
                self.send_response(code)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.login_server = ThreadingHTTPServer(("127.0.0.1", 0), Callback)
        threading.Thread(target=self.login_server.serve_forever, daemon=True).start()
        callback = f"http://127.0.0.1:{self.login_server.server_port}/callback"
        return self.website + "/desktop-login?" + urllib.parse.urlencode({"callback": callback, "state": expected_state})
