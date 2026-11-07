import tempfile
import unittest
from pathlib import Path
from app.club import ClubStore
from app.practice import PracticeRecorder, practice_stats


TRACK = {"id": "t1", "name": "Track", "min_lap_ms": 1500, "loop_width_m": .3}
MEMBER = {"id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc", "name": "Irul", "nick": "I", "tts_name": "Irul"}


class PracticeRecorderTest(unittest.TestCase):
    def recorder(self, closed=None, idle_seconds=120):
        recorder = PracticeRecorder(on_close=closed.append if closed is not None else None,
                                    idle_seconds=idle_seconds)
        recorder.tick(race_status="idle", track=TRACK, now=0)
        return recorder

    def test_practice_requires_real_track(self):
        recorder = PracticeRecorder()
        recorder.tick(race_status="idle", track=None, now=0)
        self.assertFalse(recorder.record(123, 1000, now=1)["accepted"])
        self.assertFalse(recorder.snapshot()["active"])

    def test_idle_practice_pauses_for_race_and_closes(self):
        closed = []
        recorder = self.recorder(closed)
        recorder.record(123, 1000, now=1)
        recorder.record(123, 2500, now=2)
        self.assertEqual(recorder.snapshot()["session"]["laps"]["123"], [1500])
        recorder.tick(race_status="countdown", track=TRACK, now=3)
        self.assertFalse(recorder.snapshot()["active"])
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0]["track_name"], "Track")
        self.assertEqual(closed[0]["ended_at"], 2)
        self.assertEqual(closed[0]["close_reason"], "race_started")

    def test_idle_closes_each_member_separately(self):
        closed = []
        recorder = self.recorder(closed, idle_seconds=120)
        recorder.record(123, 1000, now=0)
        recorder.record(123, 2500, now=1)
        recorder.record(456, 4000, now=50)
        recorder.record(456, 5500, now=51)
        recorder.tick(race_status="idle", track=TRACK, now=121)
        self.assertEqual(len(closed), 1)
        self.assertEqual(list(closed[0]["laps"]), ["123"])
        self.assertEqual(list(recorder.snapshot()["session"]["laps"]), ["456"])
        new_origin = recorder.record(123, 8000, now=122)
        self.assertTrue(new_origin["origin"])
        self.assertNotEqual(new_origin["session_id"], closed[0]["id"])

    def test_rejected_passings_do_not_move_baseline_or_extend_session(self):
        recorder = self.recorder(idle_seconds=10)
        recorder.record(123, 1000, now=0)
        for timestamp in (1000, 500, 1200):
            self.assertFalse(recorder.record(123, timestamp, now=1)["accepted"])
        recorder.record(123, 3000, now=2)
        self.assertEqual(recorder.snapshot()["session"]["laps"]["123"], [2000])
        self.assertFalse(recorder.record(123, 3100, now=11)["accepted"])
        recorder.tick(race_status="idle", track=TRACK, now=12)
        self.assertEqual(recorder.snapshot()["active_sessions"], [])
        self.assertEqual(len(recorder.snapshot()["history"]), 1)

    def test_identity_is_frozen_and_metrics_keep_raw_passing(self):
        recorder = self.recorder()
        recorder.record(123, 1000, member=MEMBER, now=0)
        recorder.record(123, 3000, member={**MEMBER, "id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "name": "Budi"},
                        now=1, rssi=-24.5, hit_count=8, dur_us=90000)
        driver = recorder.snapshot()["active_sessions"][0]["drivers"][0]
        self.assertEqual(driver["driver_id"], MEMBER["id"])
        self.assertEqual(driver["name"], "Irul")
        self.assertEqual(driver["passings"][-1]["rssi"], -24.5)
        self.assertEqual(driver["passings"][-1]["hit_count"], 8)
        self.assertEqual(driver["passings"][-1]["speed_kmh"], 12)
        self.assertEqual(driver["top_speed_kmh"], 12)
        self.assertIsNone(driver["top3_ms"])

    def test_track_change_and_decoder_reset_never_create_cross_boundary_lap(self):
        recorder = self.recorder()
        recorder.record(123, 1000, now=0)
        recorder.record(123, 3000, now=1)
        recorder.tick(race_status="idle", track={**TRACK, "id": "t2", "name": "Track 2"}, now=2)
        self.assertEqual(len(recorder.snapshot()["history"]), 1)
        self.assertTrue(recorder.record(123, 5000, now=3)["origin"])
        recorder.record(123, 7000, now=4)
        recorder.clock_reset(now=5)
        self.assertTrue(recorder.record(123, 0, now=6)["origin"])
        self.assertEqual(recorder.snapshot()["history"][0]["close_reason"], "decoder_interrupted")

    def test_failed_persistence_retries_on_tick(self):
        attempts = []
        def persist(session):
            attempts.append(session)
            if len(attempts) == 1:
                raise OSError("disk busy")
        recorder = PracticeRecorder(on_close=persist)
        recorder.tick(race_status="idle", track=TRACK, now=0)
        recorder.record(123, 1000, now=0)
        recorder.record(123, 3000, now=1)
        recorder.clock_reset(now=2)
        self.assertEqual(recorder.snapshot()["pending_persistence"], 1)
        recorder.tick(race_status="idle", track=TRACK, now=3)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]["id"], attempts[1]["id"])
        self.assertEqual(recorder.snapshot()["pending_persistence"], 0)

    def test_snapshot_is_detached_from_recorder_and_privacy_website_only(self):
        recorder = self.recorder()
        recorder.record(123, 1000, now=0)
        recorder.record(123, 3000, now=1)
        snapshot = recorder.snapshot()
        snapshot["session"]["drivers"][0]["name"] = "changed"
        snapshot["active_sessions"][0]["laps"]["123"].append(1)
        self.assertEqual(recorder.snapshot()["session"]["laps"]["123"], [2000])
        with self.assertRaises(ValueError):
            recorder.set_visibility("public")

    def test_statistics_use_all_valid_laps(self):
        stats = practice_stats([2000, 3000, 4000, 8000])
        self.assertEqual(stats["best_ms"], 2000)
        self.assertEqual(stats["top3_ms"], 3000)
        self.assertEqual(stats["consecutive_ms"], 3000)
        self.assertEqual(stats["p80_ms"], 4250)


class MemberStorageTest(unittest.TestCase):
    def test_tts_and_avatar_persist_locally(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "club.sqlite3"
            store = ClubStore(path)
            row = store.save_member({"name": "Irul", "nick": "I", "tts_name": "I rul", "transponder": [123], "avatar_url": "https://example.test/a.png"})
            store.close()
            store = ClubStore(path)
            self.assertEqual(store.members()[0]["tts_name"], "I rul")
            self.assertEqual(store.members()[0]["avatar_url"], "https://example.test/a.png")
            store.save_member({**row, "tts_name": "Irul"})
            self.assertEqual(store.members()[0]["tts_name"], "Irul")
            store.close()


if __name__ == "__main__":
    unittest.main()
