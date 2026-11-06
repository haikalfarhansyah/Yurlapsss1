"""Read active club members from Supabase and atomically refresh local cache."""
from __future__ import annotations

import json
import time
import urllib.parse
import uuid


def _read_all(client, table: str, params: dict) -> list[dict]:
    rows = []
    offset = 0
    while True:
        query = urllib.parse.urlencode({**params, "limit": 500, "offset": offset})
        page = client._request("/rest/v1/" + table + "?" + query)
        if not isinstance(page, list):
            raise ValueError("Respons " + table + " tidak valid")
        rows.extend(page)
        if len(page) < 500:
            return rows
        offset += len(page)


def fetch_members(client, club_id: str) -> list[dict]:
    club_id = str(uuid.UUID(club_id))
    memberships = _read_all(client, "club_members", {"select": "user_id,role,status",
                            "club_id": "eq." + club_id, "status": "eq.active",
                            "order": "user_id.asc"})
    ids = [str(uuid.UUID(m["user_id"])) for m in memberships]
    if not ids:
        return []
    profiles, transponders = [], []
    for start in range(0, len(ids), 50):
        user_filter = "in.(" + ",".join(ids[start:start + 50]) + ")"
        profiles.extend(_read_all(client, "profiles", {"select": "id,full_name,nickname,tts_name",
                                  "id": user_filter, "order": "id.asc"}))
        transponders.extend(_read_all(client, "transponders", {
            "select": "user_id,transponder_id,rc_class,rc_brand", "user_id": user_filter,
            "order": "user_id.asc,transponder_id.asc"}))
    by_id = {p["id"]: p for p in profiles}
    # A partial profile response can be an RLS/configuration issue. Never erase
    # the existing cache when the server did not return every active member.
    if any(mid not in by_id for mid in ids):
        raise ValueError("Data profil member tidak lengkap")
    devices = {mid: [] for mid in ids}
    for device in transponders:
        if device.get("user_id") in devices:
            devices[device["user_id"]].append(device)
    members = []
    for membership, mid in zip(memberships, ids):
        profile = by_id[mid]
        tps = devices[mid]
        members.append({"id": mid, "name": profile.get("full_name") or profile.get("nickname") or mid,
                        "nick": profile.get("nickname") or "", "tts_name": profile.get("tts_name") or "",
                        "transponder_details": tps, "role": membership["role"],
                        "status": "active", "kelas": tps[0].get("rc_class", "") if tps else "",
                        "mobil": tps[0].get("rc_brand", "") if tps else "",
                        "transponder": [str(t["transponder_id"]) for t in tps]})
    return members


def refresh_members(store, club_id: str, client) -> int:
    club_id = str(uuid.UUID(club_id))
    if store.club().get("club_id") != club_id:
        raise ValueError("Klub aktif tidak cocok")
    members = fetch_members(client, club_id)
    conn = store._conn
    # Check again inside a write transaction to prevent a concurrent club
    # switch from installing this club's members into a different club.
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT club_id FROM club WHERE id = 1").fetchone()
        if not row or row[0] != club_id:
            raise ValueError("Klub aktif telah berubah")
        conn.execute("DELETE FROM member WHERE source='cloud'")
        now = time.time()
        for m in members:
            conn.execute("INSERT INTO member (id, name, nick, kelas, mobil, role, status,"
                         " transponder, updated_at, tts_name, transponder_details, source) VALUES (?,?,?,?,?,?,?,?,?,?,?,'cloud')",
                         (m["id"], m["name"], m["nick"], m["kelas"], m["mobil"],
                          m["role"], m["status"], json.dumps(m["transponder"]), now,
                          m["tts_name"], json.dumps(m["transponder_details"])))
        conn.execute("UPDATE club SET synced_at = ? WHERE id = 1", (now,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return len(members)
