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
from app.race_config import normalize

class OfflineRaceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        env=patch.dict(os.environ,{'YURLAPS_DATA':self.temp.name});env.start();self.addCleanup(env.stop)
        self.store=ClubStore(Path(self.temp.name)/'club.sqlite3');self.addCleanup(self.store._conn.close)
        for mid,tp in [('Alpha',11),('Beta',22)]:
            self.store.save_member({'id':mid,'name':mid,'transponder':[tp]})
        self.store.save_track({'id':'test-track','name':'Test Track','active':True,
                               'loop_width_m':.3,'min_lap_ms':1500})
        self.engine=Engine(Config());self.ctx=AppContext(self.engine.cfg,self.engine,None,EventBus())
        self.ctx._club=self.store;self.race=self.ctx.race;self.decoder=DecoderClient(self.engine,'inproc://test')
        self.race.set_peserta([{'member_id':'Alpha'},{'member_id':'Beta'}])
        self.race.set_cfg({'target':'laps','value':3,'start_mode':'grid','cd':0})

    def status(self,ts):self.decoder._dispatch_message(f'S {ts} -41.25 5.08 77 52')
    def passing(self,ts,tp=11,duration=90000):self.decoder._dispatch_message(f'P {ts} OPN {tp} -35.5 64 {duration}')
    def go(self):
        self.status(10000)
        self.race.mulai()
        self.assertEqual(self.race.status, 'live')

    def test_unknown_transponder_is_logged_without_registration(self):
        self.passing(10001,99999)
        self.assertEqual(self.engine.pass_log[-1]['transponder_id'],99999)
        self.assertEqual(self.race.log[0]['hit_count'],64)
        self.assertEqual(len(self.store.members()),2)

    def test_decoder_reaches_race_using_timestamp_not_arrival_time(self):
        self.go();self.passing(11000);self.passing(21234)
        self.assertEqual(self.race.peserta[0]['laps'],[10234])
        self.assertEqual(self.race.klasemen()[0]['best_ms'],10234)
        self.assertEqual(self.race.klasemen()[0]['top_speed_kmh'],12)

    def test_arrival_order_does_not_choose_winner(self):
        self.go();self.passing(12000,11);self.passing(11500,22)
        self.assertEqual(self.race.klasemen()[0]['transponder'],22)
        self.passing(22000,11);self.passing(21500,22)
        rows=self.race.klasemen()
        self.assertEqual(rows[0]['transponder'],22)
        self.assertEqual(rows[1]['gap_leader'],{'ms':500})

    def test_duplicates_and_old_packets_do_not_shift_baseline(self):
        self.go()
        for t in [11000,21000,21000,19000,21100,31000]:self.passing(t)
        self.assertEqual(self.race.peserta[0]['laps'],[10000,10000])
        self.decoder._dispatch_message('P nope OPN 11 -30 20 90000')
        self.assertEqual(self.decoder.rx_counts['bad'],1)

    def test_staggered_first_passing_is_zero(self):
        self.race.set_cfg({'start_mode':'staggered'});self.go();self.passing(11000);self.passing(22000)
        self.assertEqual(self.race.peserta[0]['laps'],[11000])

    def test_decoder_restart_keeps_race_live_without_cross_boundary_lap(self):
        self.go();self.passing(11000);self.status(22000);self.status(100);self.passing(200)
        self.assertEqual(self.race.status,'live')
        self.assertEqual(self.race.peserta[0]['laps'],[])

    def test_race_can_start_without_decoder_status(self):
        self.race.mulai()
        self.assertEqual(self.race.status, 'live')

    def test_speed_is_passing_duration_and_cable_spacing(self):
        self.assertEqual(kmh(.3,90000),12)
        self.assertEqual(kmh(.5,100000),18)
        for w,d in [(None,90000),(.3,0),(.3,-1),(0,90000),(float('nan'),90000)]:self.assertIsNone(kmh(w,d))

    def test_active_track_controls_minimum_lap_time(self):
        track = self.store.tracks()[0]
        self.store.save_track({"id": track["id"], "name": track["name"],
                               "loop_width_m": .3, "min_lap_s": 2.5, "active": True})
        fresh = __import__("app.race", fromlist=["RaceEngine"]).RaceEngine(self.store)
        self.assertEqual(fresh.track_id, track["id"])
        self.assertEqual(fresh.cfg["min_lap_ms"], 2500)

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

    def test_pace_uses_every_completed_lap_without_dropping_another_baseline(self):
        d=result_rows([{'name':'A','laps':[10000,11000,12000,20000]}],{'start_mode':'grid'})[0]
        self.assertEqual(d['total_ms'],53000)
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

    def test_time_target_stops_at_decoder_cutoff(self):
        self.race.set_cfg({'target':'time','value':10});self.go();self.passing(11000)
        self.passing(19999)
        self.status(20000);self.race.cek_waktu()
        self.assertEqual(self.race.status,'done')
        self.passing(21000)
        self.assertEqual(self.race.peserta[0]['laps'],[8999])
        self.assertEqual(self.race.peserta[1]['first_decoder_ts'],None)
        saved=self.store.race(self.race.simpan()['id'])
        self.assertEqual(saved['drivers'][1]['result_status'],'DNS')

    def test_each_grid_driver_completes_own_target(self):
        self.race.set_cfg({'target':'laps','value':2});self.go()
        for ts,tp in [(11000,11),(12000,22),(21000,11),(31000,11)]:self.passing(ts,tp)
        self.assertEqual(self.race.status,'live')
        self.assertEqual(self.race.peserta[0]['finish_decoder_ts'],31000)
        self.passing(32000,22);self.passing(42000,22)
        self.assertEqual(self.race.status,'done')
        self.assertEqual([d['transponder'] for d in self.race.klasemen()],[11,22])
        self.passing(43000,11)
        self.assertEqual(self.race.log[0]['status'],'finished')
        self.assertEqual(len(self.race.peserta[0]['laps']),2)

    def test_delayed_finish_packets_rank_by_global_timestamp(self):
        self.race.set_cfg({'value':1});self.go()
        self.passing(11000,11);self.passing(12000,22)
        self.passing(22000,11);self.passing(21000,22)
        self.assertEqual([d['transponder'] for d in self.race.klasemen()],[22,11])

    def test_grid_position_does_not_rank_by_personal_total(self):
        self.go();self.passing(11000,11);self.passing(15000,22)
        self.passing(21000,11);self.passing(22000,22)
        rows=self.race.klasemen()
        self.assertEqual(rows[0]['transponder'],11)
        self.assertEqual(rows[1]['total_ms'],7000)
        self.assertEqual(rows[1]['gap_leader'],{'ms':1000})

    def test_grid_time_finish_current_lap_waits_for_each_active_driver(self):
        self.race.set_cfg({'target':'time','value':10,'finish_current_lap':True});self.go()
        self.passing(11000,11);self.status(20000);self.race.cek_waktu()
        self.assertEqual(self.race.status,'live')
        self.assertIsNotNone(self.race.peserta[1]['finish_decoder_ts'])
        self.passing(22000,11)
        self.assertEqual(self.race.status,'done')
        self.assertEqual(self.race.peserta[0]['laps'],[11000])

    def test_grid_time_cannot_start_an_unstarted_driver_after_deadline(self):
        self.race.set_cfg({'target':'time','value':10,'finish_current_lap':True});self.go()
        self.passing(21000,11)
        self.assertIsNone(self.race.peserta[0]['first_decoder_ts'])
        self.assertEqual(self.race.log[0]['status'],'finished')

    def test_staggered_time_deadlines_are_per_driver(self):
        self.race.set_cfg({'start_mode':'staggered','target':'time','value':10});self.go()
        self.passing(11000,11);self.passing(16000,22)
        self.status(21000);self.race.cek_waktu()
        self.assertEqual(self.race.status,'live')
        self.assertEqual(self.race.peserta[0]['finish_decoder_ts'],21000)
        self.assertIsNone(self.race.peserta[1]['finish_decoder_ts'])
        self.status(26000);self.race.cek_waktu()
        self.assertEqual(self.race.status,'done')
        self.assertEqual(self.race.peserta[1]['finish_decoder_ts'],26000)
        self.assertEqual(len([e for e in self.race.events if e['kind']=='driver_finish']),2)

    def test_staggered_auto_calls_do_not_start_personal_clock(self):
        self.race.set_cfg({'start_mode':'staggered','stagger_mode':'auto','stagger_gap_ms':3000});self.go()
        self.assertEqual([e['transponder'] for e in self.race.events if e['kind']=='stagger_call'],[11])
        self.status(13000);self.race.cek_waktu()
        self.assertEqual([e['transponder'] for e in self.race.events if e['kind']=='stagger_call'],[11,22])
        self.assertTrue(all(p['first_decoder_ts'] is None for p in self.race.peserta))

    def test_countdown_one_to_go_has_configured_random_delay(self):
        self.race.set_cfg({'cd':3,'random_delay_max':3})
        with patch('app.race.time.monotonic',return_value=100),patch('app.race.random.uniform',return_value=2.5):
            self.race.observe_clock(10000);self.race.mulai()
        for wall,ts,value in [(101,11000,2),(102,12000,1),(104,14000,1)]:
            with patch('app.race.time.monotonic',return_value=wall):
                self.race.observe_clock(ts);self.race.cek_waktu()
                self.assertEqual(self.race.status,'countdown')
                self.assertEqual(self.race.snapshot()['countdown_value'],value)
        with patch('app.race.time.monotonic',return_value=104.5):
            self.race.observe_clock(14500);self.race.cek_waktu()
            self.assertEqual(self.race.status,'live')
        self.assertEqual([e['value'] for e in self.race.events if e['kind']=='countdown'],[3,2,1])

    def test_decoder_gap_does_not_pause_race(self):
        self.go();self.passing(11000);self.passing(21000)
        seen=self.race.decoder_received_mono
        with patch('app.race.time.monotonic',return_value=seen+4.9):
            self.race.cek_waktu();self.assertEqual(self.race.status,'live')
        with patch('app.race.time.monotonic',return_value=seen+5):
            self.race.cek_waktu();self.assertEqual(self.race.status,'live')
        self.race.observe_clock(40000)
        self.race.catat(11,decoder_ts=41000)
        self.race.catat(11,decoder_ts=51000)
        self.assertEqual(self.race.peserta[0]['laps'],[10000,20000,10000])

    def test_older_status_than_last_passing_is_not_a_decoder_restart(self):
        self.go();self.passing(11000)
        self.status(10500)
        self.assertEqual(self.race.status,'live')
        self.assertEqual(self.race.decoder_ts,11000)

    def test_reset_requires_save_or_discard_and_keeps_results_until_reset(self):
        self.go();self.passing(11000);self.passing(21000);self.race.selesai()
        with self.assertRaises(ValueError):self.race.reset()
        self.race.simpan();self.assertEqual(self.race.peserta[0]['laps'],[10000])
        self.assertTrue(self.race.snapshot()['can_reset'])
        self.race.reset();self.assertEqual(self.race.status,'idle')
        self.assertEqual(self.race.peserta[0]['laps'],[])

    def test_position_change_announces_only_driver_currently_passing(self):
        self.go();self.passing(11000,11);self.passing(12000,22)
        self.passing(21000,22)
        events=[e for e in self.race.events if e['kind']=='position_change']
        self.assertEqual([(e['transponder'],e['position']) for e in events],[(22,1)])
        self.passing(22000,11)
        events=[e for e in self.race.events if e['kind']=='position_change']
        self.assertEqual([(e['transponder'],e['position']) for e in events],[(22,1),(11,2)])

    def test_lap_events_count_all_global_laps_and_only_new_global_records(self):
        self.go();self.passing(11000,11);self.passing(12000,22)
        self.passing(21000,11);self.passing(23000,22);self.passing(30000,11)
        laps=[e for e in self.race.events if e['kind']=='lap']
        best=[e for e in self.race.events if e['kind']=='best_lap']
        self.assertEqual([e['global_lap'] for e in laps],[1,2,3])
        self.assertEqual([e['lap_ms'] for e in best],[10000,9000])
        self.assertEqual(self.race.snapshot()['global_best']['lap_ms'],9000)

    def test_finish_events_include_grid_position_and_final_order(self):
        self.race.set_cfg({'value':1});self.go()
        self.passing(11000,11);self.passing(12000,22)
        self.passing(21000,11);self.passing(22000,22)
        finishes=[e for e in self.race.events if e['kind']=='driver_finish']
        self.assertEqual([e['position'] for e in finishes],[1,2])
        self.assertEqual([e['position'] for e in self.race.events[-1]['results']],[1,2])

    def test_preset_start_defaults_and_validation(self):
        self.assertEqual(normalize({'start_mode':'grid'})['rank'],'position')
        self.assertEqual(normalize({'start_mode':'staggered'})['rank'],'result')
        self.assertEqual(normalize({'target':'time','value':95})['target_time_ms'],95000)
        for data in ({'value':0},{'value':1.5},{'random_delay_max':4},{'stagger_gap_ms':100}):
            with self.assertRaises(ValueError):normalize(data)

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
