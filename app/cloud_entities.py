"""Versioned track/preset synchronization; all network work runs off the timing thread."""
from __future__ import annotations

import json
import time
import uuid
from .member_sync import _read_all

KINDS = {
    "track": ("club_tracks", {"name": "name", "addr": "address", "width_m": "width_m",
                             "loop_width_m": "loop_width_m", "note": "note", "best_ms": "best_ms", "holder": "holder"}),
    "preset": ("club_presets", {"name": "name", "target": "target", "value": "value",
                                "rank": "rank", "start_mode": "start_mode", "cd": "cd", "sound": "sound"}),
}


def remote_id(club_id, kind, local_id):
    try:
        return str(uuid.UUID(local_id))
    except ValueError:
        return str(uuid.uuid5(uuid.UUID(club_id), kind + ":" + local_id))


def _data(row, kind):
    return {remote: row.get(local) for local, remote in KINDS[kind][1].items()}


def _install(conn, kind, local_id, row):
    mapping = KINDS[kind][1]
    fields = list(mapping) + ["remote_revision", "synced", "deleted", "sync_error", "updated_at"]
    values = [row.get(mapping[k]) for k in mapping] + [row["revision"], 1, int(bool(row.get("deleted_at"))), None, time.time()]
    conn.execute(f"INSERT INTO {kind} (id,{','.join(fields)}) VALUES ({','.join('?' for _ in range(len(fields)+1))}) "
                 f"ON CONFLICT(id) DO UPDATE SET {','.join(k+'=excluded.'+k for k in fields)}", [local_id] + values)


def sync_entities(store, club_id, client):
    """CAS writes, durable mutation IDs, then pull. Dirty local changes are never overwritten."""
    for kind, (table, mapping) in KINDS.items():
        conn = store._conn
        pending = conn.execute(f"SELECT * FROM {kind} WHERE synced=0 AND sync_error IS NULL").fetchall()
        for raw in pending:
            row = dict(raw)
            mutation = row["mutation_id"] or str(uuid.uuid4())
            if not row["mutation_id"]:
                with conn:
                    conn.execute(f"UPDATE {kind} SET mutation_id=? WHERE id=? AND mutation_id IS NULL", (mutation, row["id"]))
            result = client._request("/rest/v1/rpc/desktop_sync_entity", "POST", {
                "p_kind": kind, "p_club_id": club_id, "p_id": remote_id(club_id, kind, row["id"]),
                "p_base_revision": row["remote_revision"], "p_mutation_id": mutation,
                "p_data": _data(row, kind), "p_deleted": bool(row["deleted"]),
            })
            if not isinstance(result, dict) or not (result.get("record") or result.get("conflict")):
                raise ValueError("Konfirmasi sinkronisasi tidak valid")
            with conn:
                if result.get("conflict"):
                    conn.execute(f"UPDATE {kind} SET sync_error=? WHERE id=? AND mutation_id=?",
                                 (json.dumps(result), row["id"], mutation))
                else:
                    record = result["record"]
                    if record.get("id") != remote_id(club_id, kind, row["id"]) or record.get("club_id") != club_id:
                        raise ValueError("Konfirmasi berasal dari club lain")
                    # A newer local edit during the request stays dirty, based on the acknowledged version.
                    conn.execute(f"UPDATE {kind} SET remote_revision=?, synced=CASE WHEN mutation_id=? THEN 1 ELSE 0 END "
                                 "WHERE id=?", (record["revision"], mutation, row["id"]))
        remote = _read_all(client, table, {"select": "*", "club_id": "eq." + club_id, "order": "id.asc"})
        conn.execute("BEGIN IMMEDIATE")
        try:
            local = {remote_id(club_id, kind, r["id"]): dict(r) for r in conn.execute(f"SELECT * FROM {kind}")}
            seen = set()
            for row in remote:
                if row.get("club_id") != club_id:
                    raise ValueError("Data berasal dari club lain")
                seen.add(row["id"])
                old = local.get(row["id"])
                if not old or old["synced"]:
                    _install(conn, kind, old["id"] if old else row["id"], row)
            # Compatibility with hard deletes from older website releases.
            for rid, old in local.items():
                if rid not in seen and old["synced"] and old["remote_revision"]:
                    conn.execute(f"UPDATE {kind} SET deleted=1 WHERE id=?", (old["id"],))
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def conflicts(store):
    out = []
    for kind in KINDS:
        out.extend({"kind": kind, "id": r["id"], "name": r["name"]} for r in store._conn.execute(
            f"SELECT id,name FROM {kind} WHERE sync_error IS NOT NULL"))
    return out


def resolve(store, kind, local_id, choice):
    if kind not in KINDS or choice not in ("local", "remote"):
        raise ValueError("Pilihan konflik tidak valid")
    conn = store._conn
    with store.lock, conn:
        row = conn.execute(f"SELECT * FROM {kind} WHERE id=?", (local_id,)).fetchone()
        if not row or not row["sync_error"]:
            raise ValueError("Konflik tidak ditemukan")
        current = json.loads(row["sync_error"]).get("current")
        if choice == "remote":
            if current:
                _install(conn, kind, local_id, current)
            else:
                conn.execute(f"UPDATE {kind} SET deleted=1, synced=1, sync_error=NULL WHERE id=?", (local_id,))
        else:
            conn.execute(f"UPDATE {kind} SET remote_revision=?, synced=0, mutation_id=?, sync_error=NULL WHERE id=?",
                         (current["revision"] if current else 0, str(uuid.uuid4()), local_id))
