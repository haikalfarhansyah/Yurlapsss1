import json
import tempfile
import time
import unittest
import urllib.parse
import urllib.request
from pathlib import Path
from unittest.mock import patch
from app.cloud import CloudService
from app.club import ClubStore
from app.race_upload import UploadError

USER='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
CLUB='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
OTHER='cccccccc-cccc-4ccc-8ccc-cccccccccccc'
class FakeClient:
    offline=False
    revoked=False
    expired=False
    uploaded={}
    def __init__(self,url,key,token): self.token=token
    def _request(self,path,method='GET',body=None):
        if self.offline: raise UploadError('offline')
        if '/token?' in path:
            if self.expired: raise UploadError('invalid refresh token',400)
            return {'access_token':'rotated','refresh_token':'rotated-refresh','expires_in':3600,'user':{'id':USER}}
        if path=='/auth/v1/user': return {'id':USER,'email':'test@example.test'}
        if 'logout' in path:return None
        if '/club_members?' in path:return [] if self.revoked else [{'club_id':CLUB,'role':'leader','status':'active'},{'club_id':OTHER,'role':'leader','status':'active'}]
        if '/clubs?' in path:return [{'id':CLUB,'name':'A'},{'id':OTHER,'name':'B'}]
        raise AssertionError(path)
    def upload(self,payload):
        if self.offline:raise UploadError('offline')
        self.uploaded[payload['id']]=payload
        return payload['id']

class CloudIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        FakeClient.offline=FakeClient.revoked=FakeClient.expired=False
        FakeClient.uploaded={}
        self.service=CloudService(Path(self.temp.name),client_factory=FakeClient)
        self.session={'access_token':'access','refresh_token':'refresh','expires_at':time.time()+3600}
    def tearDown(self):
        self.service.stop();self.temp.cleanup()
    def login(self):self.service.accept_session(self.session)
    def race(self):
        store=ClubStore(self.service.database_path())
        store.simpan_race({'id':'local-race','name':'Final','started_at':1,'drivers':[{'name':'A','transponder':123,'laps':[1000,1200]}]})
        store.close()
    def sync(self):
        with patch('app.cloud.refresh_members'),patch('app.cloud.sync_entities'):
            self.service.sync_once()
    def test_offline_restart_reconnect_upload_once(self):
        self.login();self.race();FakeClient.offline=True;self.sync()
        self.service=CloudService(Path(self.temp.name),client_factory=FakeClient)
        self.assertEqual(self.service.status()['selected_club'],CLUB)
        self.assertNotIn('access_token',self.service.status())
        FakeClient.offline=False;self.sync();self.sync()
        self.assertEqual(len(FakeClient.uploaded),1)
        store=ClubStore(self.service.database_path())
        self.assertEqual(store.race('local-race')['sync'],'terkirim');store.close()
    def test_club_switch_keeps_origin_and_uploads_all_authorized_caches(self):
        self.login();self.race();first=self.service.database_path()
        self.service.select_club(OTHER)
        self.assertNotEqual(first,self.service.database_path())
        self.sync()
        self.assertEqual(next(iter(FakeClient.uploaded.values()))['club_id'],CLUB)
    def test_revoked_leader_cannot_upload(self):
        self.login();self.race();FakeClient.revoked=True;self.sync()
        self.assertFalse(FakeClient.uploaded);self.assertIn('dicabut',self.service.last_error)
    def test_refresh_rotated_token_persisted_and_invalid_session_retains_cache(self):
        self.login();self.service.state['expires_at']=0;self.sync()
        self.assertEqual(self.service.credentials.load()['refresh_token'],'rotated-refresh')
        self.service.state['expires_at']=0;FakeClient.expired=True;self.sync()
        self.assertTrue(self.service.status()['login_required'])
        self.assertEqual(self.service.status()['selected_club'],CLUB)
    def test_logout_does_not_delete_pending_race(self):
        self.login();self.race();path=self.service.database_path();self.service.logout()
        self.assertFalse(self.service.status()['user_id'])
        store=ClubStore(path);self.assertEqual(store.race('local-race')['sync'],'belum');store.close()
    def test_cloud_cache_does_not_seed_fake_track(self):
        self.login();store=ClubStore(self.service.database_path())
        self.assertEqual(store.tracks(),[]);store.close()
    def test_callback_rejects_wrong_origin_and_replay(self):
        url=self.service.begin_login();query=urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        data=urllib.parse.urlencode({'state':query['state'][0],'session':json.dumps(self.session)}).encode()
        def send(origin):
            return urllib.request.urlopen(urllib.request.Request(query['callback'][0],data=data,headers={'Origin':origin,'Content-Type':'application/x-www-form-urlencoded'}),timeout=5)
        with self.assertRaises(urllib.error.HTTPError):send('https://evil.test')
        with send(self.service.website) as response:self.assertEqual(response.status,200)
        with self.assertRaises(urllib.error.HTTPError):send(self.service.website)
