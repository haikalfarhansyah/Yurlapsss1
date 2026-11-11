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
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def remote_race_id(club_id: str, local_id: str) -> str:
    """Stable across retries and restarts, including legacy non-UUID IDs."""
    return str(uuid.uuid5(uuid.UUID(club_id), "yurlaps-race:" + local_id))


def remote_practice_id(club_id: str, local_id: str, driver_key: str) -> str:
    """Stable remote id per local practice session and driver account/transponder."""
    return str(uuid.uuid5(uuid.UUID(club_id), "yurlaps-practice:" + local_id + ":" + driver_key))


def race_payload(race: dict, user_id: str) -> dict:
    club_id = str(uuid.UUID(race["club_id"]))
    raced_at = datetime.datetime.fromtimestamp(
        float(race["started_at"]), datetime.timezone.utc).isoformat()
    # Keep an immutable account snapshot with every result row.  A transponder
    # can be reassigned later; historical race names must continue to resolve
    # to the account that drove this session.
    result_rows = []
    for d in race.get("drivers", []):
        account_id = d.get("account_id") or d.get("user_id") or d.get("driver_id") or d.get("member_id")
        try:
            account_id = str(uuid.UUID(str(account_id))) if account_id else None
        except (ValueError, TypeError, AttributeError):
            account_id = None
        result_rows.append({
            "position": d.get("pos"),
            "name": d.get("name") or d.get("nick") or "",
            "driver": d.get("name") or d.get("nick") or "",
            "nickname": d.get("nick"), "class": d.get("kelas"),
            "user_id": account_id, "driver_id": account_id, "member_id": account_id,
            "total_laps": len(d.get("laps") or []),
            "total_ms": sum(d.get("laps") or []),
            "transponder": d.get("transponder"), "best_ms": d.get("best_ms"),
            "top3_ms": d.get("top3_ms"), "consecutive_ms": d.get("consecutive_ms"),
            "p80_ms": d.get("p80_ms"), "consistency": d.get("consistency"),
            # `kmh` is a legacy per-row field and is intentionally empty in
            # RaceEngine result rows.  Upload the calculated session metrics
            # so the website leaderboard and personal history do not lose
            # speed data.
            "speed_kmh": d.get("top_speed_kmh") or d.get("kmh"),
            "top_speed_kmh": d.get("top_speed_kmh"),
            "average_speed_kmh": d.get("average_speed_kmh"),
            "passings": d.get("passings") or [],
            "result_status": d.get("result_status"),
            "uid": d.get("uid"), "no": d.get("no"),
            "change": d.get("change"), "gap_leader": d.get("gap_leader"),
            "gap_previous": d.get("gap_previous"),
            "laps_ms": d.get("laps") or [],
        })
    return {
        "id": remote_race_id(club_id, race["id"]),
        "club_id": club_id,
        "name": race["name"],
        "track_name": race.get("track_name") or "",
        "location": "",
        "raced_at": raced_at,
        "created_by": user_id,
        "source_race_id": race["id"],
        "session_data": {"schema_version": 2, "configuration": race.get("cfg", {}),
                         "source": race.get("source"),
                         "track_id": race.get("track_id"),
                         "timing_interrupted": bool(race.get("timing_interrupted")),
                         "ended_at": race.get("ended_at"), "loop_width_m": race.get("loop_width_m"),
                         "start_decoder_ts": race.get("start_decoder_ts"),
                         "end_decoder_ts": race.get("end_decoder_ts"),
                         "finish_reason": race.get("finish_reason"),
                         "drivers": race.get("drivers", [])},
        "results": result_rows,
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
            raise UploadError(f"Supabase HTTP {exc.code}", exc.code) from exc
        except (OSError, ValueError) as exc:
            raise UploadError("Tidak dapat menghubungi Supabase") from exc

    def upload(self, payload: dict) -> str:
        receipt = self._request("/rest/v1/rpc/desktop_upload_race", "POST", {"p_payload": payload})
        if not isinstance(receipt, dict) or receipt.get("id") != payload["id"] or receipt.get("club_id") != payload["club_id"]:
            raise UploadError("Server belum mengonfirmasi hasil untuk club ini")
        return receipt["id"]

    def upload_practice(self, payload: dict) -> str:
        receipt = self._request("/rest/v1/rpc/desktop_upload_practice", "POST", {"p_payload": payload})
        if not isinstance(receipt, dict) or receipt.get("id") != payload["id"]:
            raise UploadError("Server belum mengonfirmasi practice")
        return receipt["id"]

    def fetch_races(self, club_id: str, limit: int = 500) -> list[dict]:
        """Read the club's shared race history for a newly logged-in laptop."""
        query_params = {
            "club_id": "eq." + str(uuid.UUID(club_id)),
            "select": "id,club_id,name,track_name,raced_at,results,source_race_id,session_data",
            "order": "raced_at.desc,id.asc", "limit": str(limit),
        }
        all_rows = []
        while True:
            query_params["offset"] = str(len(all_rows))
            try:
                rows = self._request("/rest/v1/races?" + urllib.parse.urlencode(query_params), "GET")
            except UploadError as exc:
                # Older deployments may not have the immutable source/session
                # columns yet. Retry the same page with legacy columns.
                if exc.status != 400 or query_params["select"] == "id,club_id,name,track_name,raced_at,results":
                    raise
                query_params["select"] = "id,club_id,name,track_name,raced_at,results"
                rows = self._request("/rest/v1/races?" + urllib.parse.urlencode(query_params), "GET")
            if not isinstance(rows, list):
                raise UploadError("Respons riwayat race tidak valid")
            all_rows.extend(rows)
            if len(rows) < limit:
                return all_rows

    def delete_race(self, club_id: str, race_id: str) -> None:
        query = urllib.parse.urlencode({"id": "eq." + str(uuid.UUID(race_id)),
                                         "club_id": "eq." + str(uuid.UUID(club_id))})
        self._request("/rest/v1/races?" + query, "DELETE", prefer="return=representation")
        remaining = self._request("/rest/v1/races?" + query + "&select=id")
        if remaining:
            raise UploadError("Penghapusan hasil belum dikonfirmasi server")


def practice_payload(session: dict, club_id: str, user_id: str, driver: dict) -> dict:
    club_id = str(uuid.UUID(club_id))
    linked_id = driver.get("driver_id")
    try:
        linked_id = str(uuid.UUID(str(linked_id))) if linked_id else None
    except (ValueError, TypeError, AttributeError):
        linked_id = None
    driver_key = str(linked_id or driver.get("transponder"))
    started = datetime.datetime.fromtimestamp(float(session["started_at"]), datetime.timezone.utc).isoformat()
    ended = (datetime.datetime.fromtimestamp(float(session["ended_at"]), datetime.timezone.utc).isoformat()
             if session.get("ended_at") is not None else None)
    from .practice import practice_stats
    metrics = practice_stats(driver.get("laps_ms") or [], driver.get("passings") or [])
    return {
        "id": remote_practice_id(club_id, session["id"], driver_key),
        "source_session_id": session["id"], "club_id": club_id,
        "user_id": linked_id,
        "track_id": session.get("track_id"), "track_name": session.get("track_name") or "",
        "started_at": started, "ended_at": ended,
        "visibility": "private",  # Initial privacy; subsequent uploads preserve the website owner's choice.
        "laps": [{"transponder": driver.get("transponder"), "driver": driver.get("driver"),
                  "nickname": driver.get("nickname"), "class": driver.get("class"),
                  "laps": list(driver.get("laps_ms") or []),
                  "lap_ms": list(driver.get("laps_ms") or []),
                  "passings": driver.get("passings") or [],
                  "user_id": linked_id, "name": driver.get("name") or driver.get("driver"),
                  "tts_name": driver.get("tts_name"), "avatar_url": driver.get("avatar_url")}],
        "summary": {**metrics, "source_session_id": session["id"], "uploaded_by": user_id,
                    "schema_version": 2, "loop_width_m": session.get("loop_width_m"),
                    "close_reason": session.get("close_reason"), "min_lap_ms": session.get("min_lap_ms")},
    }


def upload_pending_practice(store: ClubStore, club_id: str, user_id: str, client, limit: int = 200) -> int:
    club_id = str(uuid.UUID(club_id)); user_id = str(uuid.UUID(user_id)); uploaded = 0
    for session in store.practice_sessions(limit, pending_only=True):
        if session.get("club_id") != club_id:
            continue
        drivers = session.get("drivers") or []
        if not drivers:
            store.tandai_practice_terkirim(session["id"], revision=session.get("revision")); continue
        for driver in drivers:
            payload = practice_payload(session, club_id, user_id, driver)
            client.upload_practice(payload)
        store.tandai_practice_terkirim(session["id"], revision=session.get("revision"))
        uploaded += 1
    return uploaded


def upload_pending_deletions(store: ClubStore, club_id: str, client) -> int:
    deleted = 0
    for row in store.pending_race_deletions(club_id):
        client.delete_race(club_id, row["remote_id"] or remote_race_id(club_id, row["id"]))
        store.acknowledge_race_deletion(row["id"])
        deleted += 1
    return deleted


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
