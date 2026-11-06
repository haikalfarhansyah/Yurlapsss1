"""Regression coverage for decoder -> race -> archive -> PDF."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app.club import ClubStore
from app.race import kmh
from app.race_results import result_rows
from app.config import Config
from app.engine import Engine
from app.decoder import DecoderClient
from app.webapp import AppContext, EventBus

class OfflineRaceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        env=patch.dict(os.environ,{'YURLAPS_DATA':self.temp.name});env.start();self.addCleanup(env.stop)
        self.store=ClubStore(Path(self.temp.name)/'club.sqlite3');self.addCleanup(self.store._conn.close)
        for mid,tp in [('Alpha',11),('Beta',22)]:
            self.store.save_member({'id':mid,'name':mid,'transponder':[tp]})
        self.store.save_track(dict(self.store.tracks()[0],loop_width_m=.3))
        self.engine=Engine(Config());self.ctx=AppContext(self.engine.cfg,self.engine,None,EventBus())
        self.ctx._club=self.store;self.race=self.ctx.race;self.decoder=DecoderClient(self.engine,'inproc://test')
        self.race.set_peserta([{'member_id':'Alpha'},{'member_id':'Beta'}])
        self.race.set_cfg({'target':'laps','value':3,'start_mode':'grid'})

    def status(self,ts):self.decoder._dispatch_message(f'S {ts} -41.25 5.08 77 52')
    def passing(self,ts,tp=11,duration=90000):self.decoder._dispatch_message(f'P {ts} OPN {tp} -35.5 64 {duration}')
    def go(self):self.status(10000);self.race.mulai();self.race.go()

    def test_unknown_transponder_is_logged_without_registration(self):
        self.passing(10001,99999)
        self.assertEqual(self.engine.pass_log[-1]['transponder_id'],99999)
        self.assertEqual(self.race.log[0]['hit_count'],64)
        self.assertEqual(len(self.store.members()),2)

    def test_decoder_reaches_race_using_timestamp_not_arrival_time(self):
        self.go();self.passing(11000);self.passing(21234)
        self.assertEqual(self.race.peserta[0]['laps'],[1000,10234])
        self.assertEqual(self.race.klasemen()[0]['best_ms'],10234)
        self.assertEqual(self.race.klasemen()[0]['top_speed_kmh'],12)

    def test_arrival_order_does_not_choose_winner(self):
        self.go();self.passing(12000,11);self.passing(11500,22)
        self.assertEqual(self.race.klasemen()[0]['transponder'],22)

    def test_duplicates_and_old_packets_do_not_shift_baseline(self):
        self.go()
        for t in [11000,21000,21000,19000,21100,31000]:self.passing(t)
        self.assertEqual(self.race.peserta[0]['laps'],[1000,10000,10000])
        self.decoder._dispatch_message('P nope OPN 11 -30 20 90000')
        self.assertEqual(self.decoder.rx_counts['bad'],1)

    def test_staggered_first_passing_is_zero(self):
        self.race.set_cfg({'start_mode':'staggered'});self.go();self.passing(11000);self.passing(22000)
        self.assertEqual(self.race.peserta[0]['laps'],[11000])

    def test_restart_stops_race_without_computer_clock_fallback(self):
        self.go();self.passing(11000);self.status(22000);self.status(100);self.passing(200)
        self.assertEqual(self.race.status,'done')
        self.assertEqual(self.race.peserta[0]['laps'],[1000])
        self.assertIn('restart',self.race.alasan_selesai)

    def test_go_requires_fresh_decoder_status(self):
        self.race.mulai()
        with self.assertRaises(ValueError):self.race.go()
        self.status(10000);self.race.decoder_received_mono-=5
        with self.assertRaises(ValueError):self.race.go()

    def test_speed_is_passing_duration_and_cable_spacing(self):
        self.assertEqual(kmh(.3,90000),12)
        self.assertEqual(kmh(.5,100000),18)
        for w,d in [(None,90000),(.3,0),(.3,-1),(0,90000),(float('nan'),90000)]:self.assertIsNone(kmh(w,d))

    def test_archive_preserves_raw_timing_speed_and_track_snapshot(self):
        self.go();self.passing(11000,duration=90000);self.passing(21000,duration=60000)
        self.race.selesai();info=self.race.simpan('Offline final')
        store=ClubStore(self.store.path)
        try:
            race=store.race(info['id']);driver=race['drivers'][0]
            self.assertEqual(driver['top_speed_kmh'],18)
            self.assertEqual(driver['average_speed_kmh'],15)
            self.assertEqual(driver['passings'][1]['decoder_timecode'],21000)
            self.assertEqual(driver['passings'][1]['pass_duration_us'],60000)
            self.assertEqual(race['loop_width_m'],.3)
            self.assertEqual(race['start_decoder_ts'],10000)
            self.assertEqual(race['sync'],'belum')
        finally:store._conn.close()

    def test_manual_member_and_duplicate_id_validation(self):
        m=self.store.save_member({'name':'Manual','transponder':'0, 999'})
        self.assertEqual(m['transponder'],[0,999])
        self.assertEqual(self.store.member_for_transponder(999)['id'],m['id'])
        with self.assertRaises(ValueError):self.store.save_member({'name':'Duplicate','transponder':[11]})
        with self.assertRaises(ValueError):self.race.set_peserta([{'transponder':11},{'transponder':11}])

    def test_legacy_length_is_not_reinterpreted_as_loop_width(self):
        track=self.store.save_track({'name':'Legacy','width_m':42})
        self.assertIsNone(track['loop_width_m'])

    def test_pace_excludes_grid_launch_segment(self):
        d=result_rows([{'name':'A','laps':[1000,10000,11000,12000,20000]}],{'start_mode':'grid'})[0]
        self.assertEqual(d['total_ms'],54000)
        self.assertEqual(d['best_ms'],10000)
        self.assertEqual(d['top3_ms'],11000)
        self.assertEqual(d['consecutive_ms'],11000)
        self.assertEqual(d['p80_ms'],13250)

    def test_import_is_atomic_and_does_not_overwrite_manual_members(self):
        with self.assertRaises(ValueError):
            self.store.import_members([{'id':'remote1','name':'Imported','transponder':[333]},
                                       {'id':'remote2','name':'Conflict','transponder':[11]}])
        self.assertEqual(len(self.store.members()),2)
        self.assertIsNone(self.store.member_for_transponder(333))
        self.store.import_members([{'id':'remote1','name':'Imported','transponder':[333]}])
        self.assertEqual(self.store.member_for_transponder(333)['source'],'website')
        self.store.save_member({'id':'web-remote1','name':'Edited offline','transponder':[333]})
        self.assertEqual(self.store.member_for_transponder(333)['name'],'Edited offline')

    def test_time_target_uses_decoder_cutoff_and_accepts_delayed_pre_cutoff_packet(self):
        self.race.set_cfg({'target':'time','value':10});self.go();self.passing(11000)
        self.status(20500);self.race.cek_waktu()
        self.assertEqual(self.race.status,'live')
        self.passing(19999);self.passing(21000)
        self.assertEqual(self.race.peserta[0]['laps'],[1000,8999])
        self.status(22000);self.race.cek_waktu();self.assertEqual(self.race.status,'done')

    def test_v1_database_migration_preserves_members_and_does_not_invent_loop_width(self):
        import sqlite3
        from app.club import SCHEMA
        path=Path(self.temp.name)/'legacy.sqlite3'
        db=sqlite3.connect(path);db.executescript(SCHEMA)
        db.execute("INSERT INTO track(id,name,width_m) VALUES ('old','Old track',42)")
        db.execute("INSERT INTO member(id,name,transponder) VALUES ('old-member','Existing','[123]')")
        db.execute('PRAGMA user_version=1');db.commit();db.close()
        migrated=ClubStore(path)
        try:
            self.assertEqual(migrated.track('old')['width_m'],42)
            self.assertIsNone(migrated.track('old')['loop_width_m'])
            self.assertEqual(migrated.members()[0]['name'],'Existing')
        finally:migrated._conn.close()

    def test_pdf_export_is_offline(self):
        from app.race_report import render_pdf
        self.go();self.passing(11000);self.passing(21000);self.race.selesai()
        info=self.race.simpan('PDF test')
        pdf=render_pdf(self.store.race(info['id']))
        self.assertTrue(pdf.startswith(b'%PDF-'))
        self.assertGreater(len(pdf),3000)

if __name__=='__main__':unittest.main()
