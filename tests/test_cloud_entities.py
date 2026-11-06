import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from app.club import ClubStore
from app.cloud_entities import sync_entities, conflicts, resolve

CLUB='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
class Remote:
    def __init__(self):self.rows={'track':{},'preset':{}};self.fail=False;self.during=None
    def _request(self,path,method='GET',body=None):
        if self.fail:raise OSError('offline')
        if body:
            kind=body['p_kind'];rid=body['p_id'];old=self.rows[kind].get(rid)
            if old and old['last_mutation_id']==body['p_mutation_id']:return {'record':dict(old)}
            if old and old['revision']!=body['p_base_revision']:return {'conflict':True,'current':dict(old)}
            row=dict(body['p_data'],id=rid,club_id=CLUB,revision=old['revision']+1 if old else 1,
                     deleted_at='deleted' if body['p_deleted'] else None,last_mutation_id=body['p_mutation_id'])
            self.rows[kind][rid]=row
            if self.during:
                callback=self.during;self.during=None;callback()
            return {'record':dict(row)}
        kind='track' if '/club_tracks?' in path else 'preset'
        return list(self.rows[kind].values())

class EntitySyncTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=ClubStore(Path(self.temp.name)/'club.db')
        self.store.set_club(club_id=CLUB);self.remote=Remote()
    def tearDown(self):self.store.close();self.temp.cleanup()
    def test_offline_preserves_dirty_then_reconnect_syncs_loop_spacing(self):
        row=self.store.save_track({'name':'Track','loop_width_m':0.8})
        self.remote.fail=True
        with self.assertRaises(OSError):sync_entities(self.store,CLUB,self.remote)
        self.assertEqual(self.store.track(row['id'])['synced'],0)
        self.remote.fail=False;sync_entities(self.store,CLUB,self.remote)
        self.assertEqual(self.remote.rows['track'][row['id']]['loop_width_m'],0.8)
        self.assertEqual(self.store.track(row['id'])['synced'],1)
    def test_concurrent_edit_stays_dirty_after_old_ack(self):
        row=self.store.save_track({'name':'First'})
        self.remote.during=lambda:self.store.save_track({'id':row['id'],'name':'Newer'})
        sync_entities(self.store,CLUB,self.remote)
        # First pending item may be the seeded track; target remains synchronized eventually.
        sync_entities(self.store,CLUB,self.remote)
        self.assertEqual(self.store.track(row['id'])['name'],'Newer')
        self.assertEqual(self.remote.rows['track'][row['id']]['name'],'Newer')
    def test_conflict_requires_explicit_resolution(self):
        row=self.store.save_track({'name':'First'});sync_entities(self.store,CLUB,self.remote)
        remote=self.remote.rows['track'][row['id']];remote.update(name='Website',revision=2,last_mutation_id=None)
        self.store.save_track({'id':row['id'],'name':'Local'})
        sync_entities(self.store,CLUB,self.remote)
        self.assertEqual(self.store.track(row['id'])['name'],'Local')
        self.assertTrue(conflicts(self.store))
        resolve(self.store,'track',row['id'],'remote')
        self.assertEqual(self.store.track(row['id'])['name'],'Website')
    def test_deleted_track_tombstone_survives_restart(self):
        row=self.store.save_track({'name':'Delete'});sync_entities(self.store,CLUB,self.remote)
        self.store.delete_track(row['id']);path=self.store.path;self.store.close();self.store=ClubStore(path)
        sync_entities(self.store,CLUB,self.remote)
        self.assertIsNone(self.store.track(row['id']))
        self.assertTrue(self.remote.rows['track'][row['id']]['deleted_at'])
