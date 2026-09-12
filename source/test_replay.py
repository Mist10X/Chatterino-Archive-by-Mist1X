import json
from pathlib import Path
import tempfile
import unittest
import time
import threading
from unittest.mock import patch
from replay_store import Session,Reader,settings_load,settings_save,sessions,session_key,session_path,utc
from replay_worker import ReplayWorker
from replay_media import media_url,reference
from twitch_worker import TwitchWorker
from twitch_core import MessageWriter
from storage import Control

BASE=1788910000000
def stream(ch='morphe_ya',sid='100',start=BASE):
    return {'id':sid,'user_login':ch,'user_id':'123','started_at':utc(start),'title':'Тестовый эфир','type':'live'}
def message(n,ch='morphe_ya',user='someone',at=None):
    return {'kind':'message','at':at if at is not None else BASE+n*1000,'id':str(n),'user':user,'display_name':user,'channel':ch,'time_utc':utc(BASE+n*1000),'text':'Сообщение '+str(n)}

class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.data=Path(self.tmp.name)
        cfg=settings_load(self.data);cfg['channels']=['dangerlyoha','morphe_ya'];settings_save(self.data,cfg)
    def tearDown(self):self.tmp.cleanup()
    def test_selected_channels_survive_restart(self):
        cfg=settings_load(self.data);self.assertEqual(cfg['channels'],['dangerlyoha','morphe_ya'])
        cfg['channels']=['morphe_ya'];settings_save(self.data,cfg);self.assertEqual(settings_load(self.data)['channels'],['morphe_ya'])
        c=Control(self.data);c.channels=['one'];c.excluded={'two'};c.save();c=Control(self.data);self.assertEqual(c.excluded,{'two'})
    def test_sessions_restart_dedup_unicode_and_literal_search(self):
        s=Session(self.data,stream(),BASE);r=message(1);r['text']='Привет <script> % _';s.append(r);s.close()
        s=Session(self.data,stream(),BASE+2000);self.assertFalse(s.append(r));s.append(message(2));s.close()
        reader=Reader(self.data,s.key)
        try:
            self.assertEqual(reader.bounds()[2],2);self.assertEqual(len(reader.search('%')),1);self.assertEqual(reader.search('Привет')[0]['text'],r['text'])
        finally:reader.close()
    def test_moderation_only_affects_prior_messages_correct_user_and_time(self):
        s=Session(self.data,stream(),BASE)
        for i in range(1,5):s.append(message(i,user='someone' if i!=2 else 'other'))
        s.append({'kind':'delete','at':BASE+2500,'id':'2','channel':'morphe_ya','user':'other'})
        s.append({'kind':'timeout','at':BASE+3500,'duration':10,'user':'someone','channel':'morphe_ya'});s.close()
        r=Reader(self.data,s.key)
        try:
            rows=r.page(BASE+9000)
            byid={x['id']:x['seq'] for x in rows if x['kind']=='message'}
            self.assertEqual(r.dimmed(rows,BASE+2000),set())
            self.assertEqual(r.dimmed(rows,BASE+3000),{byid['2']})
            self.assertEqual(r.dimmed(rows,BASE+8000),{byid['1'],byid['2'],byid['3']})
        finally:r.close()
    def test_paging_equal_timestamps_no_losses_and_search_context(self):
        s=Session(self.data,stream(),BASE)
        for i in range(700):s.append(message(i,at=BASE+1000))
        s.close();r=Reader(self.data,s.key)
        try:
            a=r.page(BASE,'after');b=r.adjacent(a[-1]['at'],a[-1]['seq'],1);c=r.adjacent(b[-1]['at'],b[-1]['seq'],1)
            self.assertEqual(len({x['id'] for x in a+b+c}),700)
            context=r.context(b[50]['at'],b[50]['seq']);self.assertIn(b[50]['seq'],[x['seq'] for x in context])
        finally:r.close()
    def test_selected_all_users_with_irc_events_and_no_send(self):
        replay=ReplayWorker(self.data);twitch=TwitchWorker(self.data,{'enabled':True,'channels':['third'],'tray':False});twitch.replay=replay;twitch.writer=MessageWriter(self.data/'twitch')
        twitch.handle_line(f'@id=m1;tmi-sent-ts={BASE};color=#aabbcc;badges=vip/1 :untracked!x PRIVMSG #morphe_ya :Привет')
        twitch.handle_line(f'@id=m2;tmi-sent-ts={BASE} :untracked!x PRIVMSG #third :Не повтор')
        twitch.handle_line(f'@tmi-sent-ts={BASE+100};ban-duration=10 :tmi CLEARCHAT #morphe_ya :untracked')
        twitch.handle_line(f'@tmi-sent-ts={BASE+200};target-msg-id=m1;login=untracked :tmi CLEARMSG #morphe_ya :Привет')
        rows=[replay.events.get_nowait()[0] for _ in range(3)]
        self.assertEqual([r['kind'] for r in rows],['message','timeout','delete']);self.assertEqual(rows[0]['color'],'#aabbcc')
        self.assertEqual(replay.events.qsize(),0);self.assertEqual(twitch.wanted_channels(),['dangerlyoha','morphe_ya','third'])
    def test_live_discovery_resume_and_stream_boundary_moves_overlap(self):
        current=[stream()]
        worker=ReplayWorker(self.data,lambda:'test',lambda *_:current)
        worker.discover(BASE+500);worker.record(message(1),{})
        # New broadcast starts before the next status poll; IRC already delivered it.
        worker.record(message(20),{});current[:]=[stream(sid='101',start=BASE+15000)]
        worker.discover(BASE+30000)
        for s in worker.active.values():s.close()
        first=Reader(self.data,session_key('morphe_ya','100'));second=Reader(self.data,session_key('morphe_ya','101'))
        try:
            self.assertEqual([x['id'] for x in first.page(BASE+99999) if x['kind']=='message'],['1'])
            self.assertEqual([x['id'] for x in second.page(BASE+99999) if x['kind']=='message'],['20'])
        finally:first.close();second.close()
    def test_offline_buffer_and_before_stream_not_archived(self):
        worker=ReplayWorker(self.data,lambda:'test',lambda *_:[]);worker.discover(BASE)
        self.assertFalse(worker.active)
        worker.pending.append((message(1),{}));worker.api=lambda *_:[stream(start=BASE+1500)]
        worker.discover(BASE+2000)
        for s in worker.active.values():s.close()
        r=Reader(self.data,session_key('morphe_ya','100'))
        try:self.assertEqual(r.search(''),[])
        finally:r.close()
    def test_delete_only_chosen_session_and_no_recreation_same_stream(self):
        worker=ReplayWorker(self.data,lambda:'test',lambda *_:[stream(),stream('dangerlyoha','200')]);worker.discover(BASE)
        key=session_key('morphe_ya','100');worker.delete(key);worker.discover(BASE+1000)
        self.assertNotIn('morphe_ya',worker.active);self.assertFalse(session_path(self.data,key).exists());self.assertIn('dangerlyoha',worker.active)
        for s in worker.active.values():s.close()
        self.assertIn(key,settings_load(self.data)['deleted'])
        with self.assertRaises(ValueError):session_path(self.data,'../bad')
    def test_frozen_alias_reply_badges_and_asset_manifest(self):
        worker=ReplayWorker(self.data);media=worker.media;ref=reference('https://static-cdn.jtvnw.net/emoticons/v2/25/default/dark/3.0','Привет',True)
        media.catalogs={'global':{},'morphe_ya':{'Привет':ref}};media.badges={'global':{'vip/1':ref}}
        s=Session(self.data,stream(),BASE);row=message(1);row['text']='Привет';row['reply']={'state':'available','text':'Привет','user':'other'}
        media.freeze(row,{'badges':'vip/1'},s.folder);s.append(row);s.close();media.catalogs['morphe_ya']={}
        r=Reader(self.data,s.key)
        try:
            record=r.search('Привет')[0];self.assertEqual(record['emote_refs']['Привет']['key'],ref['key']);self.assertTrue(record['reply']['emote_refs']);self.assertTrue(record['badge_refs'])
            self.assertEqual(r.db.execute('SELECT count(*) FROM assets').fetchone()[0],1)
        finally:r.close()
        self.assertFalse(media_url('https://evil.invalid/test.png'));self.assertFalse(media_url('https://static-cdn.jtvnw.net@evil.invalid/badges/v1/a/3'))
    def test_personal_emote_wins_and_delayed_lookup_corrects_recording(self):
        worker=ReplayWorker(self.data);media=worker.media
        channel=reference('https://static-cdn.jtvnw.net/emoticons/v2/25/default/dark/3.0','hello',True)
        personal={**reference('https://static-cdn.jtvnw.net/emoticons/v2/26/default/dark/3.0','hello',True),
                  'personal':True,'owner_twitch_id':'42','set_id':'01ARZ3NDEKTSV4RRFFQ69G5FAV'}
        media.catalogs={'global':{},'morphe_ya':{'hello':channel}}
        session=Session(self.data,stream(),BASE);worker.active={'morphe_ya':session}
        row={**message(1),'text':'hello','user_id':'42'};worker.record(row,{})
        saved=json.loads(session.db.execute("SELECT payload FROM events WHERE msgid='1'").fetchone()[0])
        self.assertEqual(saved['emote_refs'],{});self.assertEqual(saved['_personal_pending'],['42'])
        media.personal['42']={'twitch_id':'42','observed_at':time.time(),'set_ids':[personal['set_id']],'emotes':{'hello':personal}}
        media.results.put(('personal',('42',)));worker.process_media();session.close()
        reader=Reader(self.data,session.key)
        try:
            saved=reader.search('hello')[0];self.assertEqual(saved['emote_refs']['hello']['key'],personal['key']);self.assertNotIn('_personal_pending',saved)
        finally:reader.close()
    def test_recording_batches_personal_lookups(self):
        media=ReplayWorker(self.data).media;calls=[]
        def fetch_users(uids):
            calls.append(list(uids));return {uid:{'twitch_id':uid,'observed_at':time.time(),'set_ids':[],'emotes':{}} for uid in uids}
        with patch('replay_media.fetch_personal_users',fetch_users):
            for uid in map(str,range(1,26)):media.submit(('personal',uid),('personal',uid))
            media.start()
            deadline=time.monotonic()+3
            while media.pending and time.monotonic()<deadline:time.sleep(.01)
            media.stopped.set();media.join(timeout=2)
        self.assertFalse(media.pending);self.assertEqual(sorted(map(len,calls)),[5,20]);self.assertEqual(sum(map(len,calls)),25)
    def test_pause_immediately_rejects_new_events_preserves_recording(self):
        worker=ReplayWorker(self.data,lambda:'test',lambda *_:[stream()]);worker.discover(BASE)
        worker.record(message(1),{});worker.configure({**worker.config,'enabled':False});worker.process_commands(BASE+2000);worker.push(message(3))
        self.assertFalse(worker.active);self.assertTrue(worker.events.empty());self.assertEqual(sessions(self.data)[0]['messages'],1)
    def test_graceful_shutdown_drains_received_queue(self):
        now=int(time.time()*1000)
        worker=ReplayWorker(self.data,lambda:'test',lambda *_:[stream(start=now-1000)])
        worker.media.start=lambda:None;worker.media.join=lambda **_:None;worker.media.catalog=lambda *_:None
        worker.start()
        try:
            deadline=time.monotonic()+3
            while not worker.snapshot()['active'] and time.monotonic()<deadline:time.sleep(.01)
            self.assertTrue(worker.snapshot()['active'])
            for i in range(500):worker.push(message(i,at=now+i))
        finally:worker.stopped.set();worker.join(timeout=8)
        self.assertFalse(worker.is_alive());self.assertEqual(sessions(self.data)[0]['messages'],500)
    def test_later_message_in_same_millisecond_not_dimmed(self):
        s=Session(self.data,stream(),BASE);s.append(message(1,at=BASE+1000))
        s.append({'kind':'ban','at':BASE+1000,'channel':'morphe_ya','user':'someone'})
        s.append(message(2,at=BASE+1000));s.close();r=Reader(self.data,s.key)
        try:
            rows=r.page(BASE+2000);self.assertEqual(r.dimmed(rows,BASE+2000),{rows[0]['seq']})
        finally:r.close()
