import unittest

from app.live import live_payload


class LivePayloadTests(unittest.TestCase):
    def test_event_has_priority_and_omits_private_fields(self):
        race = {"status": "live", "cfg": {"name": "Race"}, "track": {"name": "Tandem"},
                "klasemen": [{"pos": 1, "name": "Alice", "transponder": "SECRET",
                              "account_id": "PRIVATE", "n_lap": 2, "best_ms": 12000}]}
        practice = {"active_sessions": [{"drivers": [{"name": "Bob", "transponder": "HIDDEN"}]}]}
        mode, payload = live_payload(race, ({"name": "Series"}, {"name": "Final"}), practice)
        self.assertEqual(mode, "event")
        self.assertEqual(payload["stage"], "Final")
        self.assertNotIn("SECRET", str(payload))
        self.assertNotIn("PRIVATE", str(payload))

    def test_quick_precedes_practice_and_done_expires(self):
        race = {"status": "live", "cfg": {"name": "Quick"}, "klasemen": []}
        practice = {"active_sessions": [{"drivers": [{"name": "Bob", "transponder": "HIDDEN",
                                                       "n_lap": 3, "best_ms": 13000}]}]}
        self.assertEqual(live_payload(race, None, practice)[0], "quick")
        race["status"] = "done"
        mode, payload = live_payload(race, None, practice, finished_at=1, now=200)
        self.assertEqual(mode, "practice")
        self.assertEqual(payload["rows"][0]["name"], "Mobil 1")
        self.assertNotIn("Bob", str(payload))
        self.assertNotIn("HIDDEN", str(payload))


if __name__ == "__main__":
    unittest.main()
