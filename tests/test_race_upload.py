"""Tests for the offline upload queue without contacting a real server."""
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.club import ClubStore
from app.race_upload import UploadError, remote_race_id, upload_pending, practice_payload

CLUB = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OTHER = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
USER = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"


class FakeClient:
    def __init__(self, fail=False):
        self.fail = fail
        self.payloads = []

    def upload(self, payload):
        self.payloads.append(payload)
        if self.fail:
            raise UploadError("offline")
        return payload["id"]


class RaceUploadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ClubStore(pathlib.Path(self.tmp.name) / "club.sqlite3")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def save(self, rid, club):
        self.store.set_club(club_id=club)
        self.store.simpan_race({"id": rid, "name": "Race", "started_at": 1,
                                "drivers": [{"name": "Driver", "transponder": 123,
                                             "laps": [1000, 1100]}]})

    def test_failure_keeps_race_queued_after_restart(self):
        self.save("r1", CLUB)
        with self.assertRaises(UploadError):
            upload_pending(self.store, CLUB, USER, FakeClient(fail=True))
        self.assertEqual(self.store.race("r1")["sync"], "belum")
        self.store.close()
        self.store = ClubStore(pathlib.Path(self.tmp.name) / "club.sqlite3")
        client = FakeClient()
        self.assertEqual(upload_pending(self.store, CLUB, USER, client), 1)
        self.assertEqual(self.store.race("r1")["sync"], "terkirim")
        self.assertEqual(client.payloads[0]["results"][0]["laps_ms"], [1000, 1100])
        self.assertEqual(upload_pending(self.store, CLUB, USER, client), 0)

    def test_only_selected_club_is_uploaded(self):
        self.save("r1", CLUB)
        self.save("r2", OTHER)
        self.save("r3", None)
        client = FakeClient()
        self.assertEqual(upload_pending(self.store, CLUB, USER, client), 1)
        self.assertEqual(self.store.race("r2")["sync"], "belum")
        self.assertEqual(self.store.race("r3")["sync"], "belum")
        self.assertEqual(client.payloads[0]["id"], remote_race_id(CLUB, "r1"))

    def test_unconfirmed_id_stays_queued(self):
        self.save("r1", CLUB)
        client = FakeClient()
        client.upload = lambda payload: "unexpected"
        with self.assertRaises(UploadError):
            upload_pending(self.store, CLUB, USER, client)
        self.assertEqual(self.store.race("r1")["sync"], "belum")

    def test_practice_payload_preserves_driver_account_and_privacy(self):
        session = {"id": "practice-1", "track_id": "track-1", "track_name": "A",
                   "started_at": 100, "ended_at": 200, "visibility": "private"}
        driver = {"driver_id": USER, "transponder": 42, "driver": "Budi",
                  "nickname": "Budi", "class": "Mod", "laps_ms": [1000, 1100],
                  "best_ms": 1000}
        payload = practice_payload(session, CLUB, USER, driver)
        self.assertEqual(payload["user_id"], USER)
        self.assertEqual(payload["visibility"], "private")
        self.assertEqual(payload["laps"][0]["lap_ms"], [1000, 1100])
        payload = practice_payload(session, CLUB, USER, dict(driver, driver_id="u-local"))
        self.assertIsNone(payload["user_id"])


if __name__ == "__main__":
    unittest.main()
