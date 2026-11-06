import unittest
from app.practice import PracticeRecorder


class PracticeRecorderTest(unittest.TestCase):
    def test_idle_practice_pauses_for_race_and_closes(self):
        closed = []
        recorder = PracticeRecorder(on_close=closed.append)
        recorder.tick(race_status="idle", track={"id": "t1", "name": "Track"}, now=10)
        recorder.record(123, 1000)
        recorder.record(123, 2500)
        self.assertEqual(recorder.snapshot()["session"]["laps"]["123"], [1500])
        recorder.tick(race_status="countdown", track={"id": "t1"}, now=20)
        self.assertFalse(recorder.snapshot()["active"])
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0]["track_name"], "Track")

    def test_race_finish_resumes_new_idle_session(self):
        recorder = PracticeRecorder()
        recorder.tick(race_status="idle", track={"id": "t1", "name": "Track"}, now=1)
        first = recorder.snapshot()["session"]["id"]
        recorder.tick(race_status="live", track={"id": "t1"}, now=2)
        recorder.tick(race_status="idle", track={"id": "t1", "name": "Track"}, now=3)
        self.assertTrue(recorder.snapshot()["active"])
        self.assertNotEqual(first, recorder.snapshot()["session"]["id"])


if __name__ == "__main__":
    unittest.main()
