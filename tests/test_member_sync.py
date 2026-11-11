import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from app.club import ClubStore
from app.member_sync import refresh_members, refresh_visiting_drivers

CLUB = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
MEMBER = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"


class Client:
    def __init__(self, fail=False):
        self.fail = fail

    def _request(self, path):
        if "club_members?" in path:
            return [{"user_id": MEMBER, "role": "member", "status": "active"}]
        if "profiles?" in path:
            return [{"id": MEMBER, "full_name": "Pembalap", "nickname": "P"}]
        if self.fail:
            raise OSError("offline")
        return [{"user_id": MEMBER, "transponder_id": "123", "rc_class": "Mod", "rc_brand": "RC"}]


class MemberSyncTest(unittest.TestCase):
    def test_offline_guest_resolves_after_observation_expires_and_can_be_deleted(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ClubStore(pathlib.Path(folder) / "club.sqlite3")
            try:
                store.set_club(club_id=CLUB)
                store.observe_transponder(123)
                local = store.save_local_guest(123, "Nama offline", "Offline")
                store._conn.execute("UPDATE observed_transponder SET observed_at=0 WHERE transponder='123'")
                store._conn.commit()
                class VisitorClient:
                    def _request(self, path, method, body):
                        assert body["p_transponders"] == ["123"]
                        return [{"id": MEMBER, "full_name": "Nama akun", "nickname": "Nick",
                                 "tts_name": "TTS", "transponder_details": [
                                     {"transponder_id": "123", "rc_class": "Mod", "rc_brand": "RC"}]}]
                refresh_visiting_drivers(store, CLUB, VisitorClient())
                self.assertEqual(store.member_for_transponder(123)["id"], local["id"])
                self.assertEqual(next(m for m in store.members() if m["id"] == MEMBER)["nick"], "Nick")
                self.assertFalse(store.delete_local_guest(MEMBER))
                self.assertTrue(store.delete_local_guest(local["id"]))
                self.assertEqual(store.member_for_transponder(123)["id"], MEMBER)
            finally:
                store.close()

    def test_visitor_becoming_club_member_replaces_guest_alias(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ClubStore(pathlib.Path(folder) / "club.sqlite3")
            try:
                store.set_club(club_id=CLUB)
                store.observe_transponder(123)
                alias = store.save_local_guest(123, "Nama lokal", "Nama lokal")
                store._conn.execute(
                    "INSERT INTO member (id,name,status,source,transponder) VALUES (?,?,?,?,?)",
                    (MEMBER, "Profil tamu", "active", "guest", '["123"]'))
                store._conn.commit()
                self.assertEqual(store.member_for_transponder(123)["id"], alias["id"])
                self.assertEqual(refresh_members(store, CLUB, Client()), 1)
                members = store.members()
                self.assertEqual(len(members), 1)
                self.assertEqual(members[0]["id"], MEMBER)
                self.assertEqual(members[0]["source"], "cloud")
                self.assertEqual(store.member_for_transponder(123)["id"], MEMBER)
            finally:
                store.close()

    def test_refresh_and_failure_preserve_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ClubStore(pathlib.Path(folder) / "club.sqlite3")
            try:
                store.set_club(club_id=CLUB)
                self.assertEqual(refresh_members(store, CLUB, Client()), 1)
                before = store.members()
                self.assertEqual(before[0]["transponder"], ["123"])
                with self.assertRaises(OSError):
                    refresh_members(store, CLUB, Client(fail=True))
                self.assertEqual(store.members(), before)
                incomplete = Client()
                original = incomplete._request
                incomplete._request = lambda path: [] if "profiles?" in path else original(path)
                with self.assertRaises(ValueError):
                    refresh_members(store, CLUB, incomplete)
                self.assertEqual(store.members(), before)
                with self.assertRaises(ValueError):
                    refresh_members(store, "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", Client())
                self.assertEqual(store.members(), before)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
