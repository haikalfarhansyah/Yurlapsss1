import tempfile
import unittest
import uuid
import copy
from pathlib import Path

from app.club import ClubStore
from app.events import EventStore
from app.event_sync import sync_events
from app.race import RaceEngine
from app.cloud import CloudService


class EventTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "club.sqlite3"
        self.club = ClubStore(self.path)
        self.addCleanup(self.club.close)
        self.track = self.club.save_track({"name": "Tandem Track", "active": True})
        self.alice = self.club.save_member({"name": "Alice", "transponder": [101, 102]})
        self.bob = self.club.save_member({"name": "Bob", "transponder": [201]})
        self.store = EventStore(self.club)
        self.event = self.store.create({"name": "Series 1", "track_id": self.track["id"],
                                        "classes": ["Buggy", "Touring"], "best_legs": 2})
        self.class_id = self.event["classes"][0]["id"]

    def test_offline_restart_and_one_car_per_class(self):
        first = self.store.add_entry(self.event["id"], {"class_id": self.class_id,
            "member_id": self.alice["id"], "transponder": 101})
        with self.assertRaisesRegex(ValueError, "satu mobil"):
            self.store.add_entry(self.event["id"], {"class_id": self.class_id,
                "member_id": self.alice["id"], "transponder": 102})
        self.assertFalse(first["approved"])
        self.club.close()
        reopened = ClubStore(self.path)
        self.addCleanup(reopened.close)
        self.club = reopened
        self.store = EventStore(reopened)
        self.assertEqual(self.store.get(self.event["id"])["entries"][0]["transponder"], "101")

    def test_approval_checkin_and_stage_entry_validation(self):
        alice = self.store.add_entry(self.event["id"], {"class_id": self.class_id,
            "member_id": self.alice["id"], "transponder": 101})
        with self.assertRaisesRegex(ValueError, "Setujui"):
            self.store.set_entry(self.event["id"], alice["id"], checked_in=True)
        self.store.set_status(self.event["id"], "registration")
        with self.assertRaisesRegex(ValueError, "check-in"):
            self.store.set_status(self.event["id"], "running")
        self.store.set_entry(self.event["id"], alice["id"], approved=True, checked_in=True)
        with self.assertRaisesRegex(ValueError, "Pilih peserta"):
            self.store.add_stage(self.event["id"], {"class_id": self.class_id,
                "kind": "final", "name": "Final A", "group": "A"})
        stage = self.store.add_stage(self.event["id"], {"class_id": self.class_id,
            "kind": "final", "name": "Final A", "group": "A", "entry_ids": [alice["id"]]})
        self.assertEqual(stage["status"], "planned")
        self.store.remove_stage(self.event["id"], stage["id"])
        self.assertEqual(self.store.get(self.event["id"])["stages"], [])
        self.assertEqual(self.store.set_status(self.event["id"], "running")["status"], "running")

    def test_standings_use_best_legs_and_ignore_qualifying(self):
        alice = self.store.add_entry(self.event["id"], {"class_id": self.class_id,
            "member_id": self.alice["id"], "transponder": 101})
        bob = self.store.add_entry(self.event["id"], {"class_id": self.class_id,
            "member_id": self.bob["id"], "transponder": 201})
        for index, kind in enumerate(("qualifying", "final", "final", "final"), 1):
            stage = self.store.add_stage(self.event["id"], {"class_id": self.class_id,
                "kind": kind, "name": f"R{index}", "group": "A",
                "entry_ids": [alice["id"], bob["id"]]})
            race_id = f"race-{index}"
            self.club.simpan_race({"id": race_id, "name": f"R{index}", "started_at": index,
                "drivers": [
                    {"name": "Alice", "transponder": 101, "pos": 1 if index != 3 else 2,
                     "result_status": "FIN", "laps": [10000]},
                    {"name": "Bob", "transponder": 201, "pos": 2 if index != 3 else 1,
                     "result_status": "FIN", "laps": [11000]},
                ]})
            self.store.mark_stage(self.event["id"], stage["id"], "live")
            self.store.mark_stage(self.event["id"], stage["id"], "saved", race_id)
        board = self.store.standings(self.event["id"])[0]["drivers"]
        self.assertEqual(board[0]["entry_id"], alice["id"])
        self.assertEqual(board[0]["points"], 50)
        self.assertEqual(board[1]["entry_id"], bob["id"])
        self.assertEqual(board[1]["points"], 43)

    def test_event_sync_upload_pull_and_conflict(self):
        club_id = str(uuid.uuid4())
        self.club.set_club(club_id=club_id)
        event = self.store.create({"name": "Online Event", "track_id": self.track["id"],
                                   "classes": ["Buggy"]})

        class Client:
            remote = {}

            def _request(self, path, method="GET", body=None):
                if path.startswith("/rest/v1/rpc/save_club_event"):
                    key = body["p_event_id"]
                    old = self.remote.get(key)
                    if (old["revision"] if old else 0) != body["p_base_revision"]:
                        return {"conflict": True, "current": copy.deepcopy(old)}
                    row = {"id": key, "club_id": body["p_club_id"],
                           "document": copy.deepcopy(body["p_document"]),
                           "revision": (old["revision"] if old else 0) + 1}
                    self.remote[key] = row
                    return {"record": copy.deepcopy(row)}
                return copy.deepcopy(list(self.remote.values()))

        client = Client()
        sync_events(self.club, club_id, client)
        self.assertFalse(self.store.sync_state(event["id"])["pending"])
        client.remote[event["id"]]["document"]["date"] = "2026-11-12"
        client.remote[event["id"]]["document"]["revision"] += 1
        client.remote[event["id"]]["revision"] += 1
        sync_events(self.club, club_id, client)
        self.assertEqual(self.store.get(event["id"])["date"], "2026-11-12")
        self.store.set_status(event["id"], "registration")
        client.remote[event["id"]]["revision"] += 1
        client.remote[event["id"]]["document"]["revision"] += 1
        sync_events(self.club, club_id, client)
        self.assertTrue(self.store.sync_state(event["id"])["conflict"])
        self.assertEqual(self.store.get(event["id"])["status"], "registration")
        self.store.resolve_sync_conflict(event["id"], "remote")
        self.assertFalse(self.store.sync_state(event["id"])["conflict"])

    def test_event_stage_uses_race_engine_and_keeps_result(self):
        alice = self.store.add_entry(self.event["id"], {"class_id": self.class_id,
            "member_id": self.alice["id"], "transponder": 101})
        self.store.set_entry(self.event["id"], alice["id"], approved=True, checked_in=True)
        stage = self.store.add_stage(self.event["id"], {"class_id": self.class_id,
            "name": "Final A", "kind": "final", "cfg": {"target": "laps", "value": 1,
            "cd": 0, "rank": "position"}})
        self.store.set_status(self.event["id"], "registration")
        self.store.set_status(self.event["id"], "running")
        race = RaceEngine(self.club)
        race.set_track(self.track["id"])
        race.set_cfg(stage["cfg"])
        race.set_peserta([{"member_id": self.alice["id"], "transponder": 101}])
        race.mulai()
        race.catat(101, decoder_ts=10000)
        race.catat(101, decoder_ts=20000)
        result = race.simpan("Final A · Series 1")
        self.store.mark_stage(self.event["id"], stage["id"], "live")
        self.store.mark_stage(self.event["id"], stage["id"], "saved", result["id"])
        self.assertEqual(self.store.results(self.event["id"])[0]["drivers"][0]["laps"], [10000])
        self.assertEqual(self.store.standings(self.event["id"])[0]["drivers"][0]["points"], 25)
        self.assertEqual(self.store.get(self.event["id"])["published_standings"][0]["drivers"][0]["points"], 25)

    def test_offline_event_and_track_attach_on_first_login(self):
        root = self.path.parent
        local = ClubStore(root / "yurlaps_club.sqlite3")
        self.addCleanup(local.close)
        track = local.save_track({"name": "Offline Track", "active": True})
        guest = local.save_member({"name": "Guest", "transponder": [303]}, _source="local_guest")
        event = EventStore(local).create({"name": "Offline Event", "track_id": track["id"],
                                          "classes": ["Buggy"]})
        EventStore(local).add_entry(event["id"], {"class_id": event["classes"][0]["id"],
                                                   "member_id": guest["id"], "transponder": 303})
        club_id = str(uuid.uuid4())
        target = ClubStore(root / "cloud.sqlite3")
        self.addCleanup(target.close)
        target.set_club(club_id=club_id)
        CloudService(root)._migrate_unassigned_local_history(target, club_id)
        self.assertEqual(target.track(track["id"])["name"], "Offline Track")
        self.assertEqual(EventStore(target).get(event["id"])["club_id"], club_id)
        self.assertEqual(target.members()[0]["name"], "Guest")

    def test_guest_identity_change_requires_reapproval(self):
        entry = self.store.add_entry(self.event["id"], {"class_id": self.class_id,
            "member_id": self.alice["id"], "transponder": 101})
        self.store.set_entry(self.event["id"], entry["id"], approved=True, checked_in=True)
        self.club.delete_member(self.alice["id"])
        new_id = str(uuid.uuid4())
        self.club.save_member({"id": new_id, "name": "Account Owner", "transponder": [101]}, _source="cloud")
        changed = self.store.reconcile_entry(self.event["id"], entry["id"])
        self.assertEqual(changed["member_id"], new_id)
        self.assertFalse(changed["approved"])
        self.assertFalse(changed["checked_in"])


if __name__ == "__main__":
    unittest.main()
