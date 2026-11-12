"""Synchronize Event documents after race uploads; never run on timing thread."""
from __future__ import annotations

import json

from .member_sync import _read_all


def sync_events(store, club_id, client):
    conn = store._conn
    rows = [row for row in conn.execute(
        "SELECT * FROM event_meet WHERE club_id=? AND sync_error IS NULL ORDER BY updated_at,id",
        (club_id,)).fetchall()
        if json.loads(row["document_json"]).get("revision", 0) > row["synced_revision"]]
    for raw in rows:
        row = dict(raw)
        document = json.loads(row["document_json"])
        result = client._request("/rest/v1/rpc/save_club_event", "POST", {
            "p_club_id": club_id, "p_event_id": row["id"],
            "p_base_revision": row["remote_revision"], "p_document": document,
        })
        if not isinstance(result, dict) or not (result.get("record") or result.get("conflict")):
            raise ValueError("Konfirmasi sinkronisasi Event tidak valid")
        with conn:
            if result.get("conflict"):
                conn.execute("UPDATE event_meet SET sync_error=? WHERE id=?",
                             (json.dumps(result.get("current"), ensure_ascii=False), row["id"]))
                continue
            remote = result["record"]
            if remote.get("id") != row["id"] or remote.get("club_id") != club_id:
                raise ValueError("Konfirmasi Event berasal dari club lain")
            # Local edits arriving during upload remain pending.
            conn.execute("UPDATE event_meet SET remote_revision=?, "
                         "synced_revision=CASE WHEN document_json=? THEN ? ELSE synced_revision END "
                         "WHERE id=?",
                         (remote["revision"], row["document_json"], document["revision"], row["id"]))
    remote_rows = _read_all(client, "club_events", {
        "select": "id,club_id,document,revision,updated_at",
        "club_id": "eq." + club_id, "order": "id.asc",
    })
    for row in remote_rows:
        for entry in row.get("document", {}).get("entries", []):
            tp = entry.get("transponder")
            if tp is not None and str(tp).isdigit():
                store.observe_transponder(tp)
    with conn:
        for row in remote_rows:
            if row.get("club_id") != club_id or not isinstance(row.get("document"), dict):
                raise ValueError("Data Event berasal dari club lain")
            local = conn.execute("SELECT document_json,remote_revision,synced_revision FROM event_meet WHERE id=?",
                                 (row["id"],)).fetchone()
            if local and json.loads(local["document_json"]).get("revision", 0) > local["synced_revision"]:
                continue
            conn.execute("INSERT INTO event_meet(id,club_id,document_json,updated_at,remote_revision,synced_revision) "
                         "VALUES(?,?,?,strftime('%s','now'),?,?) ON CONFLICT(id) DO UPDATE SET "
                         "document_json=excluded.document_json,updated_at=excluded.updated_at,"
                         "remote_revision=excluded.remote_revision,synced_revision=excluded.synced_revision",
                         (row["id"], club_id, json.dumps(row["document"], ensure_ascii=False),
                          row["revision"], row["document"].get("revision", 0)))
