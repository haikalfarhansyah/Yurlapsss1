"""Uji alur jadwal event: rencana, pembagian grup, seeding, dan jam mulai."""
import tempfile
import unittest
import uuid
from pathlib import Path

from app.club import ClubStore
from app.events import EventStore


CLUB_ID = "11111111-1111-4111-8111-111111111111"


class EventScheduleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.club = ClubStore(Path(self.tmp.name) / "club.sqlite3")
        self.club.set_club(club_id=CLUB_ID, name="Klub Uji", role="leader")
        self.track = self.club.save_track({"name": "Sirkuit", "width_m": .3, "min_lap_ms": 1500})
        self.store = EventStore(self.club)
        self.members = [self.club.save_member({"name": f"Driver {i}", "transponder": [1000 + i]})
                        for i in range(6)]

    def tearDown(self):
        self.club.close()
        self.tmp.cleanup()

    def make_event(self, classes=("Modelfix",)):
        event = self.store.create({"name": "Event Uji", "date": "2026-11-12",
                                   "track_id": self.track["id"], "classes": list(classes)})
        for member in self.members:
            entry = self.store.add_entry(event["id"], {
                "class_id": event["classes"][0]["id"], "member_id": member["id"],
                "transponder": member["transponder"][0]})
            self.store.set_entry(event["id"], entry["id"], approved=True, checked_in=True)
        return self.store.get(event["id"])

    def plan(self, event, **overrides):
        plan = {"class_id": event["classes"][0]["id"], "kind": "qualifying", "order": 1,
                "max_group_size": 4, "bump_up": 0, "source_kind": "roster",
                "seeding": {"basis": "roster", "scope": "front", "mode": "forward",
                            "rounds": 1, "drop": 0},
                "duration_ms": 60000, "gap_ms": 300000,
                "cfg": {"name": "Jadwal uji", "target": "time", "value": 60,
                        "rank": "position", "start_mode": "grid", "cd": 5,
                        "target_time_ms": 60000}}
        seeding = dict(plan["seeding"])
        seeding.update(overrides.pop("seeding", {}))
        plan.update(overrides)
        plan["seeding"] = seeding
        return self.store.save_plan(event["id"], {"plan": plan})

    def test_plan_is_saved_with_the_event(self):
        event = self.make_event()
        plan = self.plan(event)
        stored = self.store.get(event["id"])
        self.assertEqual(len(stored["plans"]), 1)
        self.assertEqual(stored["plans"][0]["id"], plan["id"])
        self.assertEqual(stored["plans"][0]["max_group_size"], 4)

    def test_generate_splits_entries_into_sessions(self):
        event = self.make_event()
        plan = self.plan(event)
        result = self.store.generate_stages(event["id"], plan["id"])
        stages = result["stages"]
        self.assertEqual(len(stages), 2)
        self.assertEqual([stage["group"] for stage in stages], ["Grup A", "Grup B"])
        self.assertEqual(len(stages[0]["entry_ids"]), 4)
        self.assertEqual(len(stages[1]["entry_ids"]), 2)
        self.assertEqual(stages[0]["status"], "planned")
        self.assertEqual(stages[0]["seed_source"], "Roster")

    def test_schedule_shows_start_time_and_order(self):
        event = self.make_event()
        plan = self.plan(event)
        self.store.generate_stages(event["id"], plan["id"])
        self.store.set_scheme(event["id"], {"scheme": {"type": "sequential"}, "start_at": 1000})
        schedule = self.store.schedule(event["id"])
        self.assertEqual(len(schedule["rows"]), 2)
        self.assertEqual(schedule["rows"][0]["start_ms"], 1000000)
        self.assertEqual(schedule["rows"][1]["start_ms"], 1000000 + 60000 + 300000)
        self.assertEqual(schedule["rows"][0]["class_name"], "Modelfix")
        self.assertEqual(len(schedule["rows"][0]["drivers"]), 4)

    def save_stage(self, event_id, stage_id, rows):
        """Tandai sesi berjalan lalu simpan hasil racing."""
        self.store.mark_stage(event_id, stage_id, "live")
        race_id = str(uuid.uuid4())
        self.club.simpan_race({
            "id": race_id, "name": stage_id, "track_id": self.track["id"],
            "started_at": 1000, "ended_at": 2000, "cfg": {"rank": "position"},
            "drivers": rows})
        self.store.mark_stage(event_id, stage_id, "saved", race_id)
        return race_id

    def test_final_session_is_seeded_from_the_saved_qualifying(self):
        event = self.make_event()
        qualifying = self.plan(event, kind="qualifying", order=1, max_group_size=6)
        self.store.generate_stages(event["id"], qualifying["id"])
        stages = self.store.get(event["id"])["stages"]
        self.save_stage(event["id"], stages[0]["id"], [
            {"pos": 1, "name": "Driver 5", "nick": "", "transponder": "1005",
             "laps": [30000], "total_ms": 30000, "best_ms": 30000},
            {"pos": 2, "name": "Driver 3", "nick": "", "transponder": "1003",
             "laps": [31000], "total_ms": 31000, "best_ms": 31000},
            {"pos": 3, "name": "Driver 0", "nick": "", "transponder": "1000",
             "laps": [32000], "total_ms": 32000, "best_ms": 32000},
        ])
        final = self.plan(event, kind="final", order=2, max_group_size=6,
                          source_kind="qualifying",
                          seeding={"basis": "result", "scope": "front",
                                   "mode": "forward", "rounds": 1, "drop": 0})
        created = self.store.generate_stages(event["id"], final["id"])["stages"]
        entries = {e["id"]: e for e in self.store.get(event["id"])["entries"]}
        transponders = [entries[entry_id]["transponder"] for entry_id in created[0]["entry_ids"]]
        self.assertEqual(transponders[:3], ["1005", "1003", "1000"])
        self.assertIn("Hasil Kualifikasi", created[0]["seed_source"])

    def test_saved_session_reseeds_the_next_round_automatically(self):
        event = self.make_event()
        qualifying = self.plan(event, kind="qualifying", order=1, max_group_size=3)
        self.store.generate_stages(event["id"], qualifying["id"])
        final = self.plan(event, kind="final", order=2, max_group_size=3,
                          source_kind="qualifying",
                          seeding={"basis": "result", "scope": "front",
                                   "mode": "forward", "rounds": 1, "drop": 0})
        self.store.generate_stages(event["id"], final["id"])
        stages = self.store.get(event["id"])["stages"]
        final_stage = next(stage for stage in stages if stage["kind"] == "final")
        self.save_stage(event["id"], stages[0]["id"], [
            {"pos": 1, "name": "Driver 5", "nick": "", "transponder": "1005",
             "laps": [30000], "total_ms": 30000, "best_ms": 30000},
            {"pos": 2, "name": "Driver 0", "nick": "", "transponder": "1000",
             "laps": [32000], "total_ms": 32000, "best_ms": 32000},
        ])
        updated = next(stage for stage in self.store.get(event["id"])["stages"]
                       if stage["id"] == final_stage["id"])
        entries = {e["id"]: e for e in self.store.get(event["id"])["entries"]}
        transponders = [entries[entry_id]["transponder"] for entry_id in updated["entry_ids"]]
        self.assertEqual(transponders[:2], ["1005", "1000"])
        self.assertIn("Hasil Kualifikasi", updated["seed_source"])

    def test_manual_entries_survive_automatic_reseeding(self):
        event = self.make_event()
        qualifying = self.plan(event, kind="qualifying", order=1, max_group_size=3)
        self.store.generate_stages(event["id"], qualifying["id"])
        final = self.plan(event, kind="final", order=2, max_group_size=3,
                          source_kind="qualifying",
                          seeding={"basis": "result", "scope": "front",
                                   "mode": "forward", "rounds": 1, "drop": 0})
        self.store.generate_stages(event["id"], final["id"])
        stages = self.store.get(event["id"])["stages"]
        final_stage = next(stage for stage in stages if stage["kind"] == "final")
        chosen = final_stage["entry_ids"][:2]
        self.store.update_stage_entries(event["id"], final_stage["id"], chosen)
        self.save_stage(event["id"], stages[0]["id"], [
            {"pos": 1, "name": "Driver 5", "nick": "", "transponder": "1005",
             "laps": [30000], "total_ms": 30000, "best_ms": 30000},
        ])
        updated = next(stage for stage in self.store.get(event["id"])["stages"]
                       if stage["id"] == final_stage["id"])
        self.assertEqual(updated["entry_ids"], chosen)

    def test_explicit_reseed_restores_the_seeded_entries(self):
        event = self.make_event()
        plan = self.plan(event, max_group_size=6)
        self.store.generate_stages(event["id"], plan["id"])
        stage = self.store.get(event["id"])["stages"][0]
        self.store.update_stage_entries(event["id"], stage["id"], stage["entry_ids"][:2])
        self.store.reseed_stage(event["id"], stage["id"])
        updated = next(s for s in self.store.get(event["id"])["stages"] if s["id"] == stage["id"])
        self.assertEqual(len(updated["entry_ids"]), 6)

    def test_deleting_a_plan_removes_its_planned_sessions(self):
        event = self.make_event()
        plan = self.plan(event)
        self.store.generate_stages(event["id"], plan["id"])
        self.store.delete_plan(event["id"], plan["id"])
        stored = self.store.get(event["id"])
        self.assertEqual(stored["plans"], [])
        self.assertEqual(stored["stages"], [])

    def test_plan_without_approved_entries_is_rejected(self):
        event = self.store.create({"name": "Event Kosong", "date": "2026-11-12",
                                    "track_id": self.track["id"], "classes": ["Modelfix"]})
        plan = self.plan(event)
        with self.assertRaises(ValueError):
            self.store.generate_stages(event["id"], plan["id"])


if __name__ == "__main__":
    unittest.main()
