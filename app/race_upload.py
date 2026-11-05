"""Offline-first race upload primitives.

Network calls are deliberately separate from the timing engine.  The caller
supplies an authenticated Supabase access token; no service-role key is used.
Only races explicitly bound to the selected club are eligible for upload.
"""
from __future__ import annotations

import datetime
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Callable, Optional

from .club import ClubStore


class UploadError(Exception):
    pass


def remote_race_id(club_id: str, local_id: str) -> str:
    """Stable across retries and restarts, including legacy non-UUID IDs."""
    return str(uuid.uuid5(uuid.UUID(club_id), "yurlaps-race:" + local_id))


def race_payload(race: dict, user_id: str) -> dict:
    club_id = str(uuid.UUID(race["club_id"]))
    raced_at = datetime.datetime.fromtimestamp(
        float(race["started_at"]), datetime.timezone.utc).isoformat()
    return {
        "id": remote_race_id(club_id, race["id"]),
        "club_id": club_id,
        "name": race["name"],
        "track_name": race.get("track_name") or "",
        "location": "",
        "raced_at": raced_at,
        "created_by": user_id,
        "results": [{
            "position": d.get("pos"), "driver": d.get("name"),
            "nickname": d.get("nick"), "class": d.get("kelas"),
            "transponder": d.get("transponder"), "best_ms": d.get("best_ms"),
            "speed_kmh": d.get("kmh"), "laps_ms": d.get("laps") or [],
        } for d in race.get("drivers", [])],
    }


class SupabaseRaceClient:
    def __init__(self, url: str, publishable_key: str, access_token: str):
        url = url.rstrip("/")
        if not url.startswith("https://"):
            raise ValueError("Supabase URL must use HTTPS")
        if not publishable_key or not access_token:
            raise ValueError("Publishable key and user access token are required")
        self.url, self.key, self.token = url, publishable_key, access_token

    def _request(self, path: str, method: str = "GET", body=None, prefer: str = ""):
        headers = {"apikey": self.key, "Authorization": "Bearer " + self.token,
                   "Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        if prefer:
            headers["Prefer"] = prefer
        request = urllib.request.Request(self.url + path, data=data,
                                         headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raise UploadError(f"Supabase HTTP {exc.code}") from exc
        except (OSError, ValueError) as exc:
            raise UploadError("Tidak dapat menghubungi Supabase") from exc

    def upload(self, payload: dict) -> str:
        # Ignore duplicates rather than overwriting an existing race. A read
        # after the insert is required even on retries after a lost response.
        self._request("/rest/v1/races?on_conflict=id", "POST", payload,
                      "resolution=ignore-duplicates,return=minimal")
        query = urllib.parse.urlencode({"select": "id,club_id", "id": "eq." + payload["id"],
                                        "club_id": "eq." + payload["club_id"]})
        rows = self._request("/rest/v1/races?" + query)
        if not isinstance(rows, list) or len(rows) != 1 or rows[0].get("id") != payload["id"] \
                or rows[0].get("club_id") != payload["club_id"]:
            raise UploadError("Server belum mengonfirmasi hasil untuk klub ini")
        return payload["id"]


def upload_pending(store: ClubStore, club_id: str, user_id: str, client,
                   limit: int = 200) -> int:
    """Upload a batch; leave failed, unassigned and other-club races pending."""
    club_id = str(uuid.UUID(club_id))
    user_id = str(uuid.UUID(user_id))
    uploaded = 0
    rows = store._conn.execute(
        "SELECT id FROM race WHERE club_id = ? AND sync != 'terkirim' "
        "AND status = 'saved' ORDER BY started_at LIMIT ?", (club_id, limit)).fetchall()
    for row in rows:
        race = store.race(row["id"])
        if not race:
            continue
        payload = race_payload(race, user_id)
        remote_id = client.upload(payload)
        if remote_id != payload["id"]:
            raise UploadError("Server mengembalikan ID hasil yang tidak sesuai")
        store.tandai_terkirim(race["id"], remote_id)
        uploaded += 1
    return uploaded


class RaceUploadWorker(threading.Thread):
    """Retry in background. Session provider must return (club_id, user_id, client)."""
    def __init__(self, db_path: Path, session: Callable, interval: float = 30):
        super().__init__(name="race-upload", daemon=True)
        self.db_path = Path(db_path)
        self.session = session
        self.interval = max(5.0, interval)
        self.stopped = threading.Event()
        self.last_error: Optional[str] = None

    def stop(self):
        self.stopped.set()

    def run(self):
        store = ClubStore(self.db_path)
        try:
            while not self.stopped.is_set():
                try:
                    session = self.session()
                    if session:
                        upload_pending(store, *session)
                        self.last_error = None
                except Exception as exc:
                    # Keep all unconfirmed results pending; never stop timing.
                    self.last_error = str(exc)
                self.stopped.wait(self.interval)
        finally:
            store.close()
