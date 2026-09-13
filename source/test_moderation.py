import json
import tempfile
import unittest
from pathlib import Path

from moderation_store import ModerationIndex
from storage import Control, atomic_write


class ModerationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.profile=Path(self.tmp.name)
        self.data=self.profile/'Plugins/history-tools/data'
        self.data.mkdir(parents=True)
        self.index=ModerationIndex(self.data)
        self.at=1788868800000

    def tearDown(self):
        self.index.close();self.tmp.cleanup()

    def event(self,kind='ban',at=None,user='someone',channel='morphe_ya',source='chatterino',duration=None):
        return dict(kind=kind,user=user,channel=channel,at_ms=self.at if at is None else at,
                    source=source,duration=duration,context=[],raw='moderation test')

    def add(self,*events):
        with self.index.db:
            for e in events:self.index.ingest(e)

    def test_duplicate_sources_and_reban_after_unban(self):
        self.add(self.event(),self.event(),self.event(at=self.at+250,source='eventsub'))
        self.assertEqual(self.index.query()['bans'],1)
        self.add(self.event('unban',self.at+500),self.event(at=self.at+700,source='eventsub'))
        result=self.index.query()
        self.assertEqual(result['bans'],2)
        self.assertTrue(result['rows'][0]['active'])
        self.assertFalse(result['rows'][1]['active'])

    def test_same_source_duplicate_bans_and_timeouts_are_one_punishment(self):
        self.add(self.event(),self.event(at=self.at+83))
        self.add(self.event('timeout',self.at+5000,duration=600),
                 self.event('timeout',self.at+5082,duration=600))
        result=self.index.query()
        self.assertEqual(result['bans'],1)
        self.assertEqual(result['timeouts'],1)

    def test_speech_only_resolves_older_punishment_in_same_channel(self):
        self.add(self.event(),self.event('speech',self.at-1),self.event('speech',self.at+1,channel='dangerlyoha'))
        self.assertTrue(self.index.query()['rows'][0]['active'])
        self.add(self.event('speech',self.at+2))
        result=self.index.query()
        self.assertEqual(result['bans'],1)
        self.assertFalse(result['rows'][0]['active'])
        self.assertIn('снова пишет',result['rows'][0]['status'])

    def test_same_timestamp_unban_and_delayed_old_action(self):
        self.add(self.event(),self.event('unban'),self.event(at=self.at-500))
        result=self.index.query()
        self.assertEqual(result['bans'],2)
        self.assertTrue(all(not r['active'] for r in result['rows']))

    def test_timeout_expiration_unknown_duration_and_unmute(self):
        self.add(self.event('timeout',duration=60))
        self.assertTrue(self.index.query(now=self.at+59999)['rows'][0]['active'])
        self.assertFalse(self.index.query(now=self.at+60000)['rows'][0]['active'])
        self.add(self.event('timeout',self.at+61000,duration=None))
        self.assertTrue(self.index.query(now=self.at+1000000)['rows'][0]['active'])
        self.add(self.event('untimeout',self.at+62000))
        self.assertFalse(self.index.query()['rows'][0]['active'])

    def test_filters_and_counters_are_per_user_channel(self):
        self.add(self.event(),self.event(channel='dangerlyoha'),self.event(user='other'),self.event('timeout',duration=10))
        self.assertEqual(self.index.query(user='SOME')['total'],3)
        self.assertEqual(self.index.query(user='someone',channel='morphe_ya')['bans'],1)
        self.assertEqual(self.index.query(user='someone',channel='morphe_ya')['timeouts'],1)
        self.assertEqual(self.index.query(kind='timeout')['total'],1)
        self.assertEqual(self.index.query(user="' OR 1=1 --")['total'],0)

    def test_incomplete_tail_recovery_restart_and_invalid_records(self):
        ban=json.dumps(self.event())
        speech=json.dumps(self.event('speech',self.at+1))
        path=self.data/'moderation.jsonl'
        path.write_text(ban+'\n{bad json}\n'+speech[:30],encoding='utf-8')
        self.index.sync()
        self.assertEqual(self.index.query()['invalid'],1)
        self.assertTrue(self.index.query()['rows'][0]['active'])
        with path.open('a',encoding='utf-8') as f:f.write(speech[30:]+'\n')
        self.index.sync();self.index.close();self.index=ModerationIndex(self.data);self.index.sync()
        self.assertEqual(self.index.query()['bans'],1)
        self.assertFalse(self.index.query()['rows'][0]['active'])

    def test_shared_summary_has_no_fabricated_channels_or_events(self):
        self.add(dict(self.event('identity'),user_id='123'))
        misc=self.profile/'Misc';misc.mkdir()
        atomic_write(misc/'sharedbans-seen.json',json.dumps({'123':{'n':7,'at':1788868800}}))
        self.index.sync()
        result=self.index.query(user='someone')
        self.assertEqual(result['bans'],0)
        self.assertEqual(result['channels'],[])
        self.assertEqual(result['hint']['n'],7)
        atomic_write(misc/'sharedbans-seen.json','{incomplete')
        self.index.sync()
        self.assertEqual(self.index.query(user='someone')['hint']['n'],7)

    def test_context_attribution_and_before_time_are_enforced(self):
        e=self.event()
        e['context']=[{'user':'someone','channel':'morphe_ya','text':'before','time_utc':'2026-09-08T11:59:00.000Z'}]
        self.add(e)
        self.assertEqual(self.index.query()['rows'][0]['context'][0]['text'],'before')
        bad=self.event(at=self.at+10)
        bad['context']=[{'user':'other','channel':'morphe_ya','text':'wrong','time_utc':'2026-09-08T11:59:00.000Z'}]
        with self.assertRaises(ValueError):self.add(bad)

    def test_remove_user_keeps_archive_and_does_not_return_on_restart(self):
        path=self.data/'someone--morphe_ya.jsonl'
        path.write_text('{"old":"archive"}\n')
        c=Control(self.data);c.users={'someone':True};c.save()
        c.remove_user('someone')
        restored=Control(self.data)
        self.assertNotIn('someone',restored.users)
        self.assertEqual(path.read_text(),'{"old":"archive"}\n')
        restored.users['someone']=True;restored.save()
        self.assertTrue(Control(self.data).users['someone'])


if __name__=='__main__':unittest.main(verbosity=2)
