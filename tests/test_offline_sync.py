"""Regresi: kegagalan sinkronisasi tidak boleh mengubah status lokal."""
import json
import pathlib
import sqlite3
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.club import ClubStore
from app.webapp import make_handler


class OfflineSyncTest(unittest.TestCase):
    def test_existing_v1_race_is_preserved_without_guessing_its_club(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "club.sqlite3"
            store = ClubStore(path)
            store.set_club(club_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
            store.simpan_race({"id": "race-old", "name": "Race lama", "started_at": 1})
            store.close()
            # Simulate a v1 database without the new club_id column.
            conn = sqlite3.connect(path)
            conn.execute("DROP INDEX IF EXISTS idx_race_pending")
            conn.execute("ALTER TABLE race DROP COLUMN club_id")
            conn.execute("PRAGMA user_version = 1")
            conn.close()
            migrated = ClubStore(path)
            try:
                self.assertEqual(migrated.race("race-old")["name"], "Race lama")
                self.assertIsNone(migrated.race("race-old")["club_id"])
            finally:
                migrated.close()

    def test_race_keeps_original_club_when_club_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ClubStore(pathlib.Path(folder) / "club.sqlite3")
            try:
                first = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
                second = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
                store.set_club(club_id=first)
                race = {"id": "race-one", "name": "Race", "started_at": 1}
                store.simpan_race(race)
                store.set_club(club_id=second)
                store.simpan_race(race)
                self.assertEqual(store.race("race-one")["club_id"], first)
            finally:
                store.close()

    def test_invalid_demo_club_is_not_attached_to_race(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ClubStore(pathlib.Path(folder) / "club.sqlite3")
            try:
                store.set_club(club_id="club-medan")
                store.simpan_race({"id": "race-one", "name": "Race", "started_at": 1})
                self.assertIsNone(store.race("race-one")["club_id"])
            finally:
                store.close()

    def test_failed_track_sync_does_not_mark_track_synced(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ClubStore(pathlib.Path(folder) / "club.sqlite3")
            store.save_track({"name": "Track lokal"})
            ctx = SimpleNamespace(cfg=SimpleNamespace(access_token=""), engine=None,
                                  club=store)
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ctx))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(f"http://127.0.0.1:{server.server_port}/api/tracks/sync",
                                  data=b"{}", headers={"Content-Type": "application/json"})
                with self.assertRaises(HTTPError) as error:
                    urlopen(request, timeout=5)
                self.assertEqual(error.exception.code, 409)
                self.assertFalse(json.load(error.exception)["ok"])
                self.assertEqual(store.tracks()[0]["synced"], 0)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
                store.close()


if __name__ == "__main__":
    unittest.main()
