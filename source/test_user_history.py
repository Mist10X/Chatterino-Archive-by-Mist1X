import copy,datetime as dt,json,tempfile,unittest
from pathlib import Path
from moderation_store import ModerationIndex
from storage import ArchiveIndex,atomic_write
from shared_bans import Store,parse_card
from user_history import UserHistory
from replay_store import Session

AT=1788962832000
def card(channel='one',room='456',at=AT,uid='123',user='someone'):
    return parse_card({'op':'sbdoc','u':uid,'items':[{'login':channel,'room':room,'since':at//1000,
        'msgs':[{'t':'Задержано <b>не HTML</b>','ts':at//1000-1,'am':1}]}]},uid,user)
class UnifiedTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.data=Path(self.tmp.name);self.mod=ModerationIndex(self.data);self.archive=ArchiveIndex(self.data)
    def tearDown(self):self.mod.close();self.archive.close();self.tmp.cleanup()
    def add(self,kind='ban',at=AT,channel='one',**extra):
        with self.mod.db:self.mod.ingest(dict(kind=kind,user='someone',channel=channel,at_ms=at,source='chatterino',context=[],**extra))
        self.mod.combined.rematch()
    def shared(self,value=None):
        Store(self.data).save(value or card());self.mod.combined.next_scan=0;self.mod.sync()
    def test_match_enrich_and_consistent_counts(self):
        self.add();self.shared();self.shared()
        result=self.mod.query('someone',exact_user=True)
        self.assertEqual(result['bans'],1);self.assertEqual(self.mod.totals('someone')['bans'],1)
        self.assertEqual(self.mod.users()['rows'][0]['bans'],1)
        self.assertEqual(result['rows'][0]['origin'],'Наш архив + Chatterino+')
        self.assertTrue(result['rows'][0]['context'][0]['automod'])
        self.assertEqual(result['rows'][0]['context'][0]['user_id'],'123')
        self.assertEqual(self.mod.db.execute('SELECT context FROM actions').fetchone()[0],'[]')
    def test_external_channel_and_unknown_user_are_counted(self):
        self.add();self.shared(card('two','999'));self.shared(card('three','888',user='untracked'))
        self.assertEqual(self.mod.totals()['bans'],3)
        self.assertEqual(self.mod.query(channel='two')['bans'],1)
        result=self.mod.query('untracked',exact_user=True)
        self.assertEqual(result['total'],1);self.assertTrue(result['rows'][0]['shared_only'])
        self.assertEqual(result['rows'][0]['context'][0]['user_id'],'123')
        self.assertFalse(result['rows'][0]['active'])
    def test_same_user_distinct_bans_unban_and_ambiguity_not_merged(self):
        self.add(at=AT-500);self.add('unban',at=AT-100);self.shared()
        self.assertEqual(self.mod.totals()['bans'],2)
        self.add(at=AT+500)
        self.assertEqual(self.mod.totals()['bans'],3)
    def test_two_external_candidates_for_one_local_remain_separate(self):
        self.add();value=card();second=card(at=AT+1000)['items'][0];value['items'].append(second);self.shared(value)
        self.assertEqual(self.mod.totals()['bans'],3)
    def test_identity_conflict_and_late_local_arrival(self):
        self.shared();self.add();self.assertEqual(self.mod.totals()['bans'],1)
        self.add('identity',user_id='999')
        self.assertEqual(self.mod.totals()['bans'],2)
    def test_history_survives_empty_response_and_index_rebuild(self):
        self.shared();Store(self.data).save({'user':'someone','user_id':'123','fetched_at':2000000000,'items':[],'note':''})
        self.mod.close()
        for p in (self.data/'panel-cache').glob('moderation.sqlite3*'):p.unlink()
        self.mod=ModerationIndex(self.data);self.mod.sync()
        self.assertEqual(self.mod.totals()['bans'],1)
    def test_corrupt_snapshot_does_not_erase_history(self):
        self.shared();Store(self.data).path('someone').write_text('{broken')
        self.mod.combined.next_scan=0;self.mod.sync();self.assertEqual(self.mod.totals()['bans'],1)
    def test_new_response_without_text_preserves_automod(self):
        value=card();self.shared(value);value['fetched_at']+=60;value['items'][0]['messages']=[];self.shared(value)
        self.assertTrue(self.mod.query()['rows'][0]['context'][0]['automod'])
    def test_old_version_card_is_preserved_before_replacement(self):
        value=card();atomic_write(Store(self.data).path('someone'),json.dumps(value));self.mod.sync()
        self.assertTrue(list((self.data/'shared-bans/history/someone').glob('*.json')))
    def test_timeline_messages_replay_dedup_unban_and_exact_user(self):
        stamp=dt.datetime.fromtimestamp((AT-3000)/1000,dt.timezone.utc).isoformat(timespec='milliseconds')
        m={'id':'same','user':'someone','channel':'one','time_utc':stamp,'text':'Привет','display_name':'Someone'}
        atomic_write(self.data/'someone--one.jsonl',json.dumps(m)+'\n');self.archive.sync()
        session=Session(self.data,{'id':'s1','user_login':'one','started_at':dt.datetime.fromtimestamp((AT-10000)/1000,dt.timezone.utc).isoformat()},AT)
        session.append(dict(m,kind='message',at=AT-3000));session.append(dict(m,id='second',text='Позже',kind='message',at=AT-2000));session.close()
        self.add();self.add('unban',at=AT+3000);self.shared()
        history=UserHistory(self.archive,self.mod);result=history.query('someone')
        self.assertEqual(result['messages'],3) # archive/replay ID is one message, plus another and AutoMod.
        self.assertEqual(sum(r['kind']=='ban' for r in result['rows']),1)
        self.assertEqual(sum(r['kind']=='unban' for r in result['rows']),1)
        bans=history.query('someone','bans');self.assertEqual(bans['rows'][0]['context'][-1]['text'],'Задержано <b>не HTML</b>')
        self.assertEqual(history.query('someone','messages',search='позже')['total'],1)
        self.assertEqual(history.query('some')['total'],0)
    def test_last_message_from_archive_when_ban_context_empty(self):
        m={'id':'last','user':'someone','channel':'one','time_utc':dt.datetime.fromtimestamp((AT-500)/1000,dt.timezone.utc).isoformat(),'text':'Последнее'}
        atomic_write(self.data/'someone--one.jsonl',json.dumps(m)+'\n');self.archive.sync();self.add()
        row=UserHistory(self.archive,self.mod).query('someone','bans')['rows'][0]
        self.assertEqual(row['context'][-1]['text'],'Последнее')
