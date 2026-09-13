import queue,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from ban_recovery import Recovery
from moderation_store import ModerationIndex
from shared_bans import SharedService,SharedError,AuthRequired,Store
from test_shared_bans import Credentials,ACCOUNT
from test_user_history import card,AT
import test_twitch
from replay_context import enrich_bans

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.data=Path(self.tmp.name)
        self.index=ModerationIndex(self.data)
        with self.index.db:self.index.ingest(dict(kind='ban',user='someone',channel='one',at_ms=AT,source='chatterino',context=[]))
        self.phase={'phase':'reconnecting'}
        self.service=SharedService(self.data,credentials=Credentials(),connection_state=lambda:self.phase)
        self.service.account=ACCOUNT;self.recovery=Recovery(self.service);self.now=AT/1000+10
    def tearDown(self):self.recovery.close();self.index.close();self.tmp.cleanup()
    def row(self):return self.recovery.db.execute('SELECT * FROM pending').fetchone()
    def test_new_ban_fetches_without_fifteen_second_wait(self):
        self.recovery.scan(self.now)
        with patch.object(self.service,'fetch') as fetch:
            self.recovery.step(self.now);self.assertEqual(fetch.call_count,1)
            self.recovery.prioritize('someone');self.recovery.step(self.now+2);self.assertEqual(fetch.call_count,1)
            self.recovery.step(self.now+3);self.assertEqual(fetch.call_count,2)
    def test_persistent_disconnect_reconnect_and_late_automod(self):
        self.recovery.scan(self.now)
        with patch.object(self.service,'fetch',side_effect=AuthRequired('offline')) as fetch:
            self.recovery.step(self.now+6);self.assertEqual(fetch.call_count,1)
            self.assertEqual(self.row()['attempts'],0);self.assertEqual(self.row()['reason'],'connection')
        self.recovery.close();self.recovery=Recovery(self.service)
        self.phase={'phase':'connected'}
        with patch.object(self.service,'fetch',side_effect=lambda *_:Store(self.data).save(card())) as fetch:
            self.recovery.step(self.now+10);self.assertEqual(fetch.call_count,1)
        self.index.combined.next_scan=0;self.index.sync();self.recovery.scan(self.now+11)
        self.assertEqual(self.row()['state'],'complete')
        self.assertEqual(self.index.totals()['bans'],1)
        self.assertTrue(self.index.query()['rows'][0]['context'][0]['automod'])
        with patch.object(self.service,'fetch') as fetch:
            self.recovery.prioritize('someone');self.recovery.step(self.now+500);fetch.assert_not_called()
    def test_missing_context_retries_are_bounded_and_manual_old_ban(self):
        self.recovery.scan(self.now)
        with patch.object(self.service,'fetch') as fetch:
            for offset in [6,40,200,1000,5000]:self.recovery.step(self.now+offset)
            self.assertEqual(fetch.call_count,5);self.assertEqual(self.row()['state'],'unavailable')
            self.recovery.step(self.now+90000);self.assertEqual(fetch.call_count,5)
        with self.recovery.db:self.recovery.db.execute('DELETE FROM pending')
        self.recovery.scan(self.now+10*86400);self.assertIsNone(self.row())
        self.recovery.prioritize('someone');self.assertEqual(self.row()['attempts'],0)
    def test_manual_work_priority_global_pacing_and_no_account(self):
        self.recovery.scan(self.now);self.service.account=None
        with patch.object(self.service,'fetch') as fetch:
            self.recovery.step(self.now+6);fetch.assert_not_called();self.service.account=ACCOUNT
            self.service.commands.put(('fetch',('other',True)));self.recovery.step(self.now+7);fetch.assert_not_called()
            self.service.commands.get();self.recovery.step(self.now+8);self.assertEqual(fetch.call_count,1)
            self.recovery.prioritize('someone');self.recovery.step(self.now+9);self.assertEqual(fetch.call_count,1)
    def test_replay_overlay_is_literal_context_and_rejects_ambiguity(self):
        rows=[dict(seq=1,kind='ban',user='someone',channel='one',at=AT)]
        self.assertEqual(enrich_bans(self.data,rows),rows)
        Store(self.data).save(card());self.index.combined.next_scan=0;self.index.sync()
        result=enrich_bans(self.data,rows)
        self.assertEqual(len(result),1);self.assertEqual(result[0]['kind'],'ban')
        self.assertTrue(result[0]['ban_context'][0]['automod']);self.assertNotIn('ban_context',rows[0])
        with self.index.db:self.index.ingest(dict(kind='ban',user='someone',channel='one',at_ms=AT+1000,source='chatterino',context=[]))
        self.assertNotIn('ban_context',enrich_bans(self.data,rows)[0])

class IdentityTests(unittest.TestCase):
    def test_held_message_author_resolves_from_ban_without_chat_or_twitch_lookup(self):
        fixture=test_twitch.TwitchTests();fixture.setUp()
        try:
            worker=fixture.worker()
            line='@target-user-id=123;tmi-sent-ts=1788962832000 :tmi.twitch.tv CLEARCHAT #morphe_ya :someone'
            worker.handle_line(line);worker.handle_line(line)
            self.assertEqual(len((fixture.data/'twitch/identities.jsonl').read_text().splitlines()),1)
            index=ModerationIndex(fixture.data)
            try:
                index.sync();service=SharedService(fixture.data,credentials=Credentials())
                with patch.object(service.api,'user_id',side_effect=AssertionError('Unexpected lookup')):self.assertEqual(service.resolve('someone'),'123')
                self.assertEqual(index.totals()['bans'],0)
            finally:index.close()
        finally:fixture.tearDown()

if __name__=='__main__':unittest.main()
