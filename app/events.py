"""Offline event plans and race links. Decoder timing stays in RaceEngine."""
from __future__ import annotations

import json
import time
import uuid

from .race_config import normalize


KINDS = {"practice", "qualifying", "heat", "main", "final"}
STATUSES = {"draft", "registration", "running", "completed"}


def _id():
    return str(uuid.uuid4())


def _label(value, name, limit=100):
    value = str(value or "").strip()
    if not value or len(value) > limit:
        raise ValueError(f"{name} wajib diisi (maksimal {limit} karakter)")
    return value


def _points(raw):
    if raw in (None, ""):
        return [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]
    if isinstance(raw, str):
        raw = raw.split(",")
    if not isinstance(raw, list) or not 1 <= len(raw) <= 50:
        raise ValueError("Tabel poin harus berisi 1 sampai 50 posisi")
    try:
        values = [int(str(value).strip()) for value in raw]
    except (TypeError, ValueError):
        raise ValueError("Tabel poin harus berupa angka bulat")
    if any(value < 0 for value in values):
        raise ValueError("Poin tidak boleh negatif")
    return values


class EventStore:
    def __init__(self, club):
        self.club = club

    def _load(self, event_id):
        row = self.club._conn.execute(
            "SELECT document_json FROM event_meet WHERE id=?", (event_id,)).fetchone()
        if not row:
            raise ValueError("Event tidak ditemukan")
        return json.loads(row[0])

    def _save(self, event):
        event["updated_at"] = time.time()
        event["revision"] = int(event.get("revision", 0)) + 1
        self.club._conn.execute(
            "INSERT INTO event_meet(id,club_id,document_json,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET club_id=excluded.club_id, "
            "document_json=excluded.document_json, updated_at=excluded.updated_at",
            (event["id"], event.get("club_id"), json.dumps(event, ensure_ascii=False),
             event["updated_at"]))
        self.club._conn.commit()
        return event

    def list(self):
        with self.club.lock:
            club_id = self.club.club().get("club_id")
            rows = self.club._conn.execute(
                "SELECT document_json FROM event_meet WHERE club_id IS ? "
                "ORDER BY updated_at DESC", (club_id,)).fetchall()
            return [json.loads(row[0]) for row in rows]

    def get(self, event_id):
        with self.club.lock:
            event = self._load(event_id)
            if event.get("club_id") != self.club.club().get("club_id"):
                raise ValueError("Event milik club lain")
            return event

    def sync_state(self, event_id):
        with self.club.lock:
            row = self.club._conn.execute(
                "SELECT remote_revision,synced_revision,sync_error FROM event_meet WHERE id=?",
                (event_id,)).fetchone()
            if not row:
                raise ValueError("Event tidak ditemukan")
            return {"pending": self.get(event_id).get("revision", 0) > row["synced_revision"],
                    "conflict": bool(row["sync_error"]), "remote_revision": row["remote_revision"]}

    def resolve_sync_conflict(self, event_id, choice):
        with self.club.lock:
            event = self.get(event_id)
            row = self.club._conn.execute(
                "SELECT sync_error FROM event_meet WHERE id=?", (event_id,)).fetchone()
            if not row or not row["sync_error"]:
                raise ValueError("Tidak ada konflik Event")
            remote = json.loads(row["sync_error"])
            if not isinstance(remote, dict) or remote.get("id") != event_id or remote.get("club_id") != event["club_id"]:
                raise ValueError("Data konflik tidak valid")
            if choice == "remote":
                document = remote.get("document")
                if not isinstance(document, dict):
                    raise ValueError("Data website tidak lengkap")
                self.club._conn.execute("UPDATE event_meet SET document_json=?,remote_revision=?,"
                    "synced_revision=?,sync_error=NULL WHERE id=?", (
                    json.dumps(document, ensure_ascii=False), remote["revision"],
                    document.get("revision", 0), event_id))
            elif choice == "local":
                self.club._conn.execute("UPDATE event_meet SET remote_revision=?,sync_error=NULL WHERE id=?",
                                        (remote["revision"], event_id))
            else:
                raise ValueError("Pilih versi website atau versi aplikasi")
            self.club._conn.commit()
            return self.get(event_id)

    def create(self, data):
        with self.club.lock:
            track_id = str(data.get("track_id") or "")
            track = self.club.track(track_id)
            if not track:
                raise ValueError("Pilih track yang tersedia")
            classes = data.get("classes") or []
            if not isinstance(classes, list) or not classes:
                raise ValueError("Tambahkan minimal satu kelas")
            names = [_label(item, "Nama kelas", 60) for item in classes]
            if len(set(x.casefold() for x in names)) != len(names):
                raise ValueError("Nama kelas tidak boleh sama")
            event = {
                "id": _id(), "club_id": self.club.club().get("club_id"),
                "name": _label(data.get("name"), "Nama event"),
                "date": str(data.get("date") or ""), "track_id": track_id,
                "track_name": track["name"], "status": "draft",
                "format": data.get("format") if data.get("format") in ("qualifying_main", "heat_final") else "qualifying_main",
                "best_legs": max(1, min(10, int(data.get("best_legs") or 1))),
                "points_table": _points(data.get("points_table")),
                "classes": [{"id": _id(), "name": name} for name in names],
                "entries": [], "stages": [], "created_at": time.time(), "revision": 0,
            }
            return self._save(event)

    def set_status(self, event_id, status):
        with self.club.lock:
            event = self.get(event_id)
            if status not in STATUSES:
                raise ValueError("Status event tidak dikenal")
            transitions = {
                "draft": {"registration"}, "registration": {"draft", "running"},
                "running": {"completed"}, "completed": set(),
            }
            if status not in transitions[event["status"]]:
                raise ValueError("Perubahan status event tidak diizinkan")
            if status == "running" and not any(e["approved"] and e["checked_in"] for e in event["entries"]):
                raise ValueError("Setujui dan check-in minimal satu peserta")
            if status == "completed" and any(s["status"] == "live" for s in event["stages"]):
                raise ValueError("Selesaikan sesi yang sedang berjalan")
            if status == "completed" and not any(s["status"] == "saved" for s in event["stages"]):
                raise ValueError("Simpan minimal satu hasil sesi sebelum menutup event")
            event["status"] = status
            return self._save(event)

    def add_entry(self, event_id, data):
        with self.club.lock:
            event = self.get(event_id)
            if event["status"] not in ("draft", "registration"):
                raise ValueError("Pendaftaran sudah ditutup")
            class_id = str(data.get("class_id") or "")
            if not any(c["id"] == class_id for c in event["classes"]):
                raise ValueError("Kelas tidak ditemukan")
            member_id = str(data.get("member_id") or "")
            member = next((m for m in self.club.members() if m["id"] == member_id), None)
            if not member:
                raise ValueError("Pilih member atau tamu yang terdaftar")
            tp = str(data.get("transponder") or "").strip()
            if tp not in {str(x) for x in member.get("transponder", [])}:
                raise ValueError("Transponder tidak terdaftar untuk peserta ini")
            if any(e["class_id"] == class_id and e["member_id"] == member_id for e in event["entries"]):
                raise ValueError("Pembalap hanya boleh satu mobil per kelas")
            if any(e["class_id"] == class_id and e["transponder"] == tp for e in event["entries"]):
                raise ValueError("Transponder sudah dipakai di kelas ini")
            details = next((d for d in member.get("transponder_details", [])
                            if str(d.get("transponder_id")) == tp), {})
            entry = {
                "id": _id(), "class_id": class_id, "member_id": member_id,
                "account_id": member.get("account_id"), "name": member["name"],
                "nick": member.get("nick") or "", "tts_name": member.get("tts_name") or member["name"],
                "transponder": tp, "car_class": details.get("rc_class") or member.get("kelas") or "",
                "car_brand": details.get("rc_brand") or member.get("mobil") or "",
                "approved": False, "checked_in": False, "source": member.get("source") or "local",
            }
            event["entries"].append(entry)
            self._save(event)
            return entry

    def set_entry(self, event_id, entry_id, *, approved=None, checked_in=None):
        with self.club.lock:
            event = self.get(event_id)
            if event["status"] == "completed":
                raise ValueError("Event sudah selesai")
            entry = next((e for e in event["entries"] if e["id"] == entry_id), None)
            if not entry:
                raise ValueError("Peserta tidak ditemukan")
            if approved is not None:
                entry["approved"] = bool(approved)
                if not entry["approved"]:
                    entry["checked_in"] = False
            if checked_in is not None:
                if checked_in and not entry["approved"]:
                    raise ValueError("Setujui peserta sebelum check-in")
                entry["checked_in"] = bool(checked_in)
            self._save(event)
            return entry

    def remove_entry(self, event_id, entry_id):
        with self.club.lock:
            event = self.get(event_id)
            if event["status"] not in ("draft", "registration"):
                raise ValueError("Peserta tidak bisa dihapus setelah event dimulai")
            before = len(event["entries"])
            event["entries"] = [e for e in event["entries"] if e["id"] != entry_id]
            if len(event["entries"]) == before:
                raise ValueError("Peserta tidak ditemukan")
            return self._save(event)

    def reconcile_entry(self, event_id, entry_id):
        with self.club.lock:
            event = self.get(event_id)
            if event["status"] == "completed":
                raise ValueError("Event sudah selesai")
            entry = next((e for e in event["entries"] if e["id"] == entry_id), None)
            if not entry:
                raise ValueError("Peserta tidak ditemukan")
            if any(stage["status"] == "saved" and stage["class_id"] == entry["class_id"]
                   for stage in event["stages"]):
                raise ValueError("Identitas peserta kelas ini sudah dipakai dalam hasil tersimpan")
            owner = self.club.member_for_transponder(entry["transponder"])
            if not owner:
                raise ValueError("Akun transponder belum ditemukan. Sinkronkan dahulu.")
            entry.update(member_id=owner["id"], account_id=owner["id"] if owner.get("source") in ("cloud", "guest") else None,
                         name=owner["name"], nick=owner.get("nick") or "",
                         tts_name=owner.get("tts_name") or owner["name"],
                         source=owner.get("source") or "local", approved=False, checked_in=False)
            self._save(event)
            return entry

    def add_stage(self, event_id, data):
        with self.club.lock:
            event = self.get(event_id)
            if event["status"] == "completed":
                raise ValueError("Event sudah selesai")
            class_id = str(data.get("class_id") or "")
            if not any(c["id"] == class_id for c in event["classes"]):
                raise ValueError("Pilih kelas event")
            kind = str(data.get("kind") or "")
            if kind not in KINDS:
                raise ValueError("Jenis sesi tidak dikenal")
            group = _label(data.get("group") or "Semua", "Grup", 30)
            cfg = normalize(data.get("cfg") or {})
            stage = {"id": _id(), "class_id": class_id, "kind": kind,
                     "name": _label(data.get("name"), "Nama sesi"), "group": group,
                     "cfg": cfg, "status": "planned", "race_id": None,
                     "entry_ids": list(data.get("entry_ids") or []),
                     "sequence": len(event["stages"]) + 1}
            if group != "Semua" and not stage["entry_ids"]:
                raise ValueError("Pilih peserta untuk grup ini")
            eligible = {e["id"] for e in event["entries"] if e["class_id"] == class_id}
            if any(x not in eligible for x in stage["entry_ids"]):
                raise ValueError("Peserta sesi harus berasal dari kelas yang dipilih")
            event["stages"].append(stage)
            self._save(event)
            return stage

    def stage(self, event_id, stage_id):
        event = self.get(event_id)
        stage = next((s for s in event["stages"] if s["id"] == stage_id), None)
        if not stage:
            raise ValueError("Sesi event tidak ditemukan")
        return event, stage

    def remove_stage(self, event_id, stage_id):
        with self.club.lock:
            event, stage = self.stage(event_id, stage_id)
            if event["status"] == "completed" or stage["status"] != "planned":
                raise ValueError("Hanya sesi terjadwal yang bisa dihapus")
            event["stages"] = [s for s in event["stages"] if s["id"] != stage_id]
            for index, item in enumerate(event["stages"], 1):
                item["sequence"] = index
            return self._save(event)

    def mark_stage(self, event_id, stage_id, status, race_id=None):
        with self.club.lock:
            event, stage = self.stage(event_id, stage_id)
            if status == "live" and stage["status"] != "planned":
                raise ValueError("Sesi ini sudah pernah dijalankan")
            if status == "saved" and stage["status"] != "live":
                raise ValueError("Sesi belum berjalan")
            if status not in ("live", "saved"):
                raise ValueError("Status sesi tidak valid")
            stage["status"] = status
            if race_id:
                if not self.club.race(race_id):
                    raise ValueError("Hasil race tidak ditemukan")
                stage["race_id"] = race_id
            self._save(event)
            if status == "saved":
                event["published_standings"] = self.standings(event_id)
                self._save(event)
            return stage

    def retry_interrupted_stage(self, event_id, stage_id):
        with self.club.lock:
            event, stage = self.stage(event_id, stage_id)
            if stage["status"] != "live":
                raise ValueError("Sesi tidak perlu dipulihkan")
            stage["status"] = "planned"
            stage["interrupted_at"] = time.time()
            self._save(event)
            return stage

    def race_is_linked(self, race_id):
        return any(stage.get("race_id") == race_id for event in self.list()
                   for stage in event["stages"])

    def live_stage(self):
        return next(((event, stage) for event in self.list()
                     for stage in event["stages"] if stage["status"] == "live"), None)

    def results(self, event_id):
        event = self.get(event_id)
        output = []
        for stage in event["stages"]:
            race = self.club.race(stage["race_id"]) if stage.get("race_id") else None
            output.append({"stage_id": stage["id"], "name": stage["name"],
                           "class_id": stage["class_id"], "kind": stage["kind"],
                           "group": stage["group"], "status": stage["status"],
                           "race_id": stage.get("race_id"),
                           "drivers": race.get("drivers", []) if race else []})
        return output

    def standings(self, event_id):
        event = self.get(event_id)
        entries = {str(e["transponder"]): e for e in event["entries"]}
        groups = {}
        for result in self.results(event_id):
            if result["status"] != "saved" or result["kind"] not in ("main", "final"):
                continue
            key = (result["class_id"], result["group"])
            bucket = groups.setdefault(key, {})
            for driver in result["drivers"]:
                entry = entries.get(str(driver.get("transponder")))
                if not entry or entry["class_id"] != result["class_id"]:
                    continue
                row = bucket.setdefault(entry["id"], {"entry_id": entry["id"],
                    "name": entry["name"], "nick": entry["nick"],
                    "transponder": entry["transponder"], "scores": []})
                position = driver.get("pos")
                points = 0 if driver.get("result_status") in ("DNS", "DNF", "DSQ") else (
                    event["points_table"][position-1] if isinstance(position, int)
                    and 1 <= position <= len(event["points_table"]) else 0)
                row["scores"].append(points)
        out = []
        for (class_id, group), rows in groups.items():
            ranked = []
            for row in rows.values():
                row["points"] = sum(sorted(row["scores"], reverse=True)[:event["best_legs"]])
                row["best_leg"] = max(row["scores"], default=0)
                ranked.append(row)
            ranked.sort(key=lambda row: (-row["points"], -row["best_leg"], row["name"].casefold()))
            for position, row in enumerate(ranked, 1):
                row["position"] = position
            out.append({"class_id": class_id, "group": group, "drivers": ranked})
        return out
