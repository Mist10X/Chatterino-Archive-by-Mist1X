import json
from pathlib import Path
import tempfile
import unittest
from deletions import DeletionCapture
from moderation_store import ModerationIndex
from twitch_core import MessageWriter
from twitch_worker import TwitchWorker


class DeletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.data=Path(self.tmp.name);self.capture=DeletionCapture(self.data/'twitch')
        self.message={'user':'someone','channel':'one','id':'message1','text':'Local','display_name':'Someone','time_utc':'2026-09-08T12:00:00Z',
            'reply':{'state':'available','channel':'one','user':'other','text':'original reply'}}
        self.tags={'target-msg-id':'message1','login':'someone','tmi-sent-ts':'1788868801000'}
    def tearDown(self):self.tmp.cleanup()
    def event(self):return self.capture.event(['#one','Local'],self.tags)
    def test_cached_original_reply_and_fallback_text(self):
        self.capture.remember(self.message);event=self.event()
        self.assertEqual(event['message']['reply']['text'],'original reply');self.assertEqual(event['kind'],'delete')
        self.capture.buffer.clear();event=self.event();self.assertEqual(event['message']['text'],'Local')
        self.assertEqual(event['message']['time_utc'],'');self.assertNotIn('reply',event['message'])
        event=self.capture.event(['#one'],self.tags);self.assertEqual(event['message']['state'],'unavailable')
    def test_all_users_capture_only_individual_deletions_in_selected_channels(self):
        w=TwitchWorker(self.data,{'enabled':True,'channels':['one'],'tray':False});w.writer=MessageWriter(self.data/'twitch')
        w.handle_line('@id=message1;tmi-sent-ts=1788868800000 :someone!s@s PRIVMSG #one :Local')
        self.assertEqual(w.snapshot()['saved'],0)
        w.handle_line('@login=someone;target-msg-id=message1;tmi-sent-ts=1788868801000 :tmi.twitch.tv CLEARMSG #one :Local')
        w.handle_line('@login=someone;target-msg-id=message1;tmi-sent-ts=1788868801000 :tmi.twitch.tv CLEARMSG #one :Local')
        w.handle_line('@target-user-id=3;tmi-sent-ts=1788868801000 :tmi.twitch.tv CLEARCHAT #one :someone')
        w.handle_line('@login=someone;target-msg-id=other;tmi-sent-ts=1788868801000 :tmi.twitch.tv CLEARMSG #two :Local')
        self.assertEqual(w.snapshot()['deleted'],1)
        w.config['enabled']=False;w.handle_line('@login=someone;target-msg-id=more;tmi-sent-ts=1788868801000 :tmi.twitch.tv CLEARMSG #one :Local')
        self.assertEqual(w.snapshot()['deleted'],1)
    def test_ids_deduplicate_across_restart_and_enrich_context(self):
        self.capture.append(self.event());index=ModerationIndex(self.data);index.sync();index.close()
        second=DeletionCapture(self.data/'twitch');second.remember(self.message);second.append(second.event(['#one','Local'],self.tags))
        index=ModerationIndex(self.data)
        try:
            index.sync();self.assertEqual(index.totals('someone'),{'bans':0,'timeouts':0,'deletions':1})
            row=index.query('someone',kind='delete')['rows'][0];self.assertEqual(row['message']['reply']['text'],'original reply')
            tags=dict(self.tags,**{'target-msg-id':'message2'});second.append(second.event(['#one','Local'],tags));index.sync()
            self.assertEqual(index.totals('someone')['deletions'],2)
        finally:index.close()
    def test_deletion_never_clears_ban_and_related_action_is_same_pair_within_minute(self):
        index=ModerationIndex(self.data);at=int(self.tags['tmi-sent-ts'])
        try:
            with index.db:
                index.ingest({'kind':'ban','user':'someone','channel':'one','at_ms':at-1000})
                index.ingest(self.event())
                index.ingest({'kind':'timeout','user':'someone','channel':'two','at_ms':at+5000,'duration':10})
            self.assertTrue(index.query('someone','one','ban')['rows'][0]['active'])
            self.assertIn('не зафиксировано',index.query('someone','one','delete')['rows'][0]['status'])
            with index.db:index.ingest({'kind':'timeout','user':'someone','channel':'one','at_ms':at+20000,'duration':10})
            self.assertIn('Через 20 с',index.query('someone','one','delete')['rows'][0]['status'])
            self.assertEqual(index.totals('someone')['deletions'],1)
        finally:index.close()
    def test_buffer_eviction_and_channel_removal(self):
        now=[0];c=DeletionCapture(self.data/'other',limit=2,ttl=10,clock=lambda:now[0])
        for i in range(3):c.remember(dict(self.message,id=str(i)))
        self.assertEqual(len(c.buffer),2);self.assertNotIn(('one','0'),c.buffer)
        now[0]=11;c.prune();self.assertFalse(c.buffer)
        c.remember(self.message);c.retain_channels([]);self.assertFalse(c.buffer)
    def test_partial_tail_and_missing_author_are_preserved_without_inventing_identity(self):
        self.capture.path.parent.mkdir(parents=True);self.capture.path.write_bytes(b'{partial')
        e=self.capture.event(['#one'],{'target-msg-id':'unknown','tmi-sent-ts':'1788868801000'})
        self.assertEqual(e['user'],'');self.capture.append(e)
        self.assertEqual(next(self.capture.path.parent.glob('*.partial-*')).read_bytes(),b'{partial')
        index=ModerationIndex(self.data)
        try:
            index.sync();self.assertEqual(index.query(kind='delete')['total'],1);self.assertEqual(index.users()['total'],0)
            self.assertEqual(index.query(kind='delete')['rows'][0]['message']['state'],'unavailable')
        finally:index.close()
    def test_attribution_mismatch_rejected(self):
        e=self.event();e['message']['user']='other';index=ModerationIndex(self.data)
        try:
            with self.assertRaises(ValueError):index.ingest(e)
        finally:index.close()

if __name__=='__main__':unittest.main()
