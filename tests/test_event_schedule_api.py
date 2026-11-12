"""Uji endpoint jadwal event lewat HTTP (jalur yang dipakai UI aplikasi).

Jalankan:  python -m unittest tests.test_event_schedule_api
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["YURLAPS_DATA"] = tempfile.mkdtemp(prefix="stintlab-schedule-")

from app.config import Config                          # noqa: E402
from app.engine import Engine                          # noqa: E402
from app.storage import Storage                        # noqa: E402
from app.webapp import AppContext, EventBus, start_server  # noqa: E402

CLUB_ID = "11111111-1111-4111-8111-111111111111"


class EventScheduleApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cfg = Config.load()
        cfg.host = "127.0.0.1"
        cfg.port = 0
        cfg.access_token = ""
        bus = EventBus()
        engine = Engine(cfg, storage=Storage(), on_event=bus.publish)
        cls.ctx = AppContext(cfg, engine, Storage(), bus)
        httpd = start_server(cls.ctx, "127.0.0.1", 0, block=False)
        cls.base = f"http://127.0.0.1:{httpd.server_address[1]}"
        club = cls.ctx.club
        club.set_club(club_id=CLUB_ID, name="Klub Uji", role="leader")
        cls.track = club.save_track({"name": "Sirkuit", "width_m": .3, "min_lap_ms": 1500})
        cls.members = [club.save_member({"name": f"Driver {i}", "transponder": [1000 + i]})
                       for i in range(5)]

    @classmethod
    def tearDownClass(cls):
        try:
            cls.ctx.httpd.shutdown()
        except Exception:
            pass

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as response:
            return response.read()

    def post(self, path, body):
        request = urllib.request.Request(self.base + path, data=json.dumps(body).encode(),
                                         method="POST")
        request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())

    def make_event(self):
        event = self.post("/api/event/create", {
            "name": "Event API", "date": "2026-11-12", "track_id": self.track["id"],
            "classes": ["Modelfix"]})["event"]
        for member in self.members:
            entry = self.post("/api/event/entry/add", {
                "event_id": event["id"], "class_id": event["classes"][0]["id"],
                "member_id": member["id"], "transponder": member["transponder"][0]})["entry"]
            self.post("/api/event/entry/set", {"event_id": event["id"], "entry_id": entry["id"],
                                               "approved": True, "checked_in": True})
        return event

    def test_schedule_endpoints(self):
        event = self.make_event()
        plan = self.post("/api/event/plan/save", {"event_id": event["id"], "plan": {
            "class_id": event["classes"][0]["id"], "kind": "qualifying", "order": 1,
            "max_group_size": 3, "bump_up": 0, "source_kind": "roster",
            "seeding": {"basis": "roster", "scope": "front", "mode": "forward",
                        "rounds": 1, "drop": 0},
            "duration_ms": 60000, "gap_ms": 300000,
            "cfg": {"name": "Jadwal API", "target": "time", "value": 60,
                    "rank": "position", "start_mode": "grid", "cd": 5,
                    "target_time_ms": 60000}}})["plan"]
        created = self.post("/api/event/generate", {"event_id": event["id"],
                                                    "plan_id": plan["id"]})
        self.assertEqual(len(created["stages"]), 2)
        self.post("/api/event/schedule/scheme", {
            "event_id": event["id"], "scheme": {"type": "sequential"}, "start_at": 1000})
        schedule = json.loads(self.get(f"/api/event/schedule?id={event['id']}"))["schedule"]
        self.assertEqual(len(schedule["rows"]), 2)
        self.assertEqual(schedule["rows"][0]["start_ms"], 1000000)
        self.assertEqual(schedule["rows"][1]["start_ms"], 1000000 + 60000 + 300000)
        self.assertEqual(len(schedule["rows"][0]["drivers"]), 3)
        self.assertEqual(schedule["rows"][0]["seed_source"], "Roster")

    def test_schedule_pdf_is_a_pdf(self):
        event = self.make_event()
        body = self.get(f"/api/event/schedule/pdf?id={event['id']}")
        self.assertTrue(body.startswith(b"%PDF"))

    def test_unknown_event_returns_error(self):
        try:
            self.get("/api/event/schedule?id=tidak-ada")
            self.fail("Event yang tidak dikenal harus ditolak")
        except urllib.error.HTTPError as error:
            self.assertEqual(error.code, 404)


if __name__ == "__main__":
    unittest.main()
