"""Production regressions: stable practice checkpoints and durable deletion."""
import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path

from app.club import ClubStore
from app.config import Config
from app.race_upload import (UploadError, practice_payload, remote_race_id,
                             upload_pending, upload_pending_deletions,
                             upload_pending_practice)
from app.webapp import AppContext, EventBus

CLUB = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
USER = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


class Engine:
    on_event = None
    def log(self, *args):
        pass


class Client:
    def __init__(self):
        self.payloads = []
        self.deleted = []
    def upload(self, payload):
        self.payloads.append(payload)
        return payload["id"]
    def upload_practice(self, payload):
        self.payloads.append(payload)
        return payload["id"]
    def delete_race(self, club, race):
        self.deleted.append((club, race))


class ProductionSyncTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "club.sqlite3"
        self.store = ClubStore(self.path)
        self.store.set_club(club_id=CLUB)
        self.track = self.store.save_track({"name": "Track A", "active": True,
                                            "min_lap_s": 2, "loop_width_m": .3})
        self.bus = EventBus()
        self.engine = Engine()
        self.ctx = AppContext(Config(), self.engine, None, self.bus)
        self.ctx._club = self.store
        self.ctx._cloud = type("Cloud", (), {"wake": threading.Event()})()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def practice(self):
        self.ctx.practice.tick(race_status="idle", track=self.track, now=10)
        self.ctx.practice.record(123, 1000, member={"id": USER, "name": "Irul"}, now=10)
        self.ctx.practice.record(123, 4000, now=11, rssi=-20, hit_count=8, dur_us=90000)

    def test_checkpoints_and_close_update_same_driver_session(self):
        self.practice()
        sid = self.ctx.practice.snapshot()["active_sessions"][0]["id"]
        self.ctx._maybe_persist_practice()
        self.assertEqual([s["id"] for s in self.store.practice_sessions()], [sid])
        revision = self.store.practice(sid)["revision"]
        self.ctx._last_practice_persist = 0
        self.ctx._maybe_persist_practice()
        self.assertEqual(self.store.practice(sid)["revision"], revision)
        self.ctx.practice.close_all("idle", now=140)
        rows = self.store.practice_sessions()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], sid)
        self.assertEqual(rows[0]["ended_at"], 11)
        self.assertEqual(rows[0]["drivers"][0]["name"], "Irul")

    def test_upload_ack_cannot_hide_newer_checkpoint(self):
        self.practice()
        self.ctx._maybe_persist_practice()
        session = self.store.practice_sessions()[0]
        snapshot = copy.deepcopy(session)
        snapshot["drivers"][0]["laps_ms"].append(2500)
        snapshot["laps"]["123"].append(2500)
        self.store.simpan_practice(snapshot)
        self.assertFalse(self.store.tandai_practice_terkirim(session["id"], revision=session["revision"]))
        self.assertEqual(self.store.practice(session["id"])["sync"], "belum")
        client = Client()
        self.assertEqual(upload_pending_practice(self.store, CLUB, USER, client), 1)
        self.assertEqual(client.payloads[0]["summary"]["lap_count"], 2)

    def test_practice_payload_contains_full_details_and_private_initial_visibility(self):
        self.practice()
        session = self.ctx.practice.snapshot()["active_sessions"][0]
        payload = practice_payload(session, CLUB, USER, session["drivers"][0])
        self.assertIsNone(payload["ended_at"])
        self.assertEqual(payload["user_id"], USER)
        self.assertEqual(payload["visibility"], "private")
        self.assertEqual(payload["summary"]["best_ms"], 3000)
        self.assertEqual(payload["summary"]["top_speed_kmh"], 12)
        self.assertEqual(payload["laps"][0]["passings"][-1]["hit_count"], 8)

    def test_race_delete_survives_offline_restart_and_is_club_scoped(self):
        self.store.simpan_race({"id": "r1", "name": "Race", "started_at": 1,
                               "track_id": self.track["id"],
                               "drivers": [{"name": "Irul", "transponder": 123, "laps": [3000]}]})
        self.store.hapus_race("r1")
        self.store.close()
        self.store = ClubStore(self.path)
        self.assertIsNone(self.store.race("r1"))
        client = Client()
        self.assertEqual(upload_pending_deletions(self.store, USER, client), 0)
        self.assertEqual(upload_pending_deletions(self.store, CLUB, client), 1)
        self.assertEqual(client.deleted, [(CLUB, remote_race_id(CLUB, "r1"))])
        self.assertEqual(self.store.pending_race_deletions(CLUB), [])

    def test_delete_during_upload_remains_queued_for_remote_cleanup(self):
        self.store.simpan_race({"id": "r1", "name": "Race", "started_at": 1, "drivers": []})
        client = Client()
        def concurrent_delete(payload):
            self.store.hapus_race("r1")
            return payload["id"]
        client.upload = concurrent_delete
        upload_pending(self.store, CLUB, USER, client)
        self.assertEqual(len(self.store.pending_race_deletions(CLUB)), 1)

    def test_idle_passing_has_one_authoritative_beep_event_even_when_ignored(self):
        subscriber = self.bus.subscribe()
        for timestamp in (1000, 1000, 2000, 4000):
            self.engine.on_event({"kind": "passing", "transponder_id": 123,
                                  "decoder_timecode": timestamp})
        events = [subscriber.get_nowait() for _ in range(subscriber.qsize())]
        self.assertEqual(len(events), 4)
        self.assertTrue(all(e["kind"] == "race_passing" for e in events))
        self.assertEqual([e["result"]["diterima"] for e in events], [True, False, False, True])
        self.assertTrue(all(e["source"] == "practice" for e in events))

    def test_active_track_changes_practice_baseline_and_settings(self):
        self.ctx.tick()
        self.ctx.practice.record(123, 1000)
        self.ctx.practice.record(123, 4000)
        track_b = self.store.save_track({"name": "Track B", "active": True,
                                         "min_lap_s": 5, "loop_width_m": .4})
        self.ctx.tick()
        self.assertEqual(self.ctx.race.track_id, track_b["id"])
        self.assertEqual(self.ctx.practice.min_lap_ms, 5000)
        self.assertTrue(self.ctx.practice.record(123, 6000)["origin"])
        self.store.delete_track(track_b["id"])
        self.store.delete_track(self.track["id"])
        self.ctx.tick()
        self.assertIsNone(self.ctx.race.track_id)
        self.assertFalse(self.ctx.practice.active)


if __name__ == "__main__":
    unittest.main()
