import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.club import ClubStore
from app.config import Config
from app.decoder import DecoderClient
from app.race_import import import_lapbeeps
from app.webapp import AppContext, EventBus, make_handler

CLUB = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'


class RecoveryImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ClubStore(Path(self.tmp.name) / 'club.sqlite3')
        self.store.set_club(club_id=CLUB)
        self.track = self.store.save_track({'name': 'Actual track', 'active': True})

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_receiver_survives_callback_error_without_replaying(self):
        engine = Mock()
        engine.on_passing.side_effect = [RuntimeError('database failure'), None]
        receiver = DecoderClient(engine, 'tcp://127.0.0.1:5556')
        with patch('app.config.default_data_dir', return_value=Path(self.tmp.name)):
            with self.assertLogs('app.decoder', level='ERROR'):
                self.assertFalse(receiver.process_message('P 100 OPN 123 -20 5 90000'))
            self.assertTrue(receiver.process_message('P 200 OPN 123 -20 5 90000'))
        self.assertEqual(engine.on_passing.call_count, 2)
        self.assertEqual(receiver.rx_counts['P'], 2)
        self.assertIn('database failure', (Path(self.tmp.name)/'receiver-errors.log').read_text())

    def test_status_poll_acquires_context_before_store(self):
        engine = Mock(on_event=None)
        ctx = AppContext(Config(), engine, None, EventBus())
        ctx._club = self.store
        ctx._cloud = Mock(status=lambda: {})
        lock = self.store.lock
        test = self
        class OrderedLock:
            def __enter__(self):
                test.assertTrue(ctx._club_lock._is_owned(), 'store/context lock inversion')
                lock.acquire()
            def __exit__(self, *args):
                lock.release()
        self.store.lock = OrderedLock()
        handler = object.__new__(make_handler(ctx))
        handler.path = '/api/cloud/status'
        handler._cloud_local = lambda: True
        handler._origin_ok = lambda: True
        handler._token_ok = lambda qs: True
        handler._json = lambda data: data
        try:
            self.assertTrue(handler.do_GET()['ok'])
        finally:
            self.store.lock = lock

    def document(self):
        return {'format': 'yurlaps-lapbeeps-v1', 'name': 'Final',
                'started_at': '2026-11-08T16:45:00+07:00',
                'drivers': [{'no': 1, 'name': 'One', 'raw_laps_ms': [1000, 30000, 29000]},
                            {'no': 2, 'name': 'Two', 'raw_laps_ms': [4000, 28000, 29000]},
                            {'no': 3, 'name': 'DNS', 'raw_laps_ms': []}]}

    def test_import_removes_only_origin_and_preserves_grid_order_and_identity(self):
        doc = self.document()
        result = import_lapbeeps(self.store, doc, self.track['id'])
        race = self.store.race(result['id'])
        self.assertEqual([d['name'] for d in race['drivers']], ['One', 'Two', 'DNS'])
        self.assertEqual(race['drivers'][0]['laps'], [30000, 29000])
        self.assertEqual(race['drivers'][0]['total_ms'], 59000)
        self.assertEqual(race['drivers'][1]['gap_leader'], {'ms': 1000})
        self.assertEqual(race['drivers'][2]['result_status'], 'DNS')
        self.assertEqual(race['drivers'][0]['transponder'], -1)
        self.assertIsNone(race['drivers'][0]['account_id'])
        self.assertEqual(race['sync'], 'belum')
        self.assertTrue(import_lapbeeps(self.store, doc, self.track['id'])['already_imported'])
        self.assertEqual(self.store.ringkasan()['races'], 1)

    def test_bad_import_cannot_partially_save(self):
        doc = self.document()
        doc['drivers'][1]['raw_laps_ms'] = [1000, -5]
        with self.assertRaises(ValueError):
            import_lapbeeps(self.store, doc, self.track['id'])
        self.assertEqual(self.store.ringkasan()['races'], 0)
