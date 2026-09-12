import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from moderation_store import ModerationIndex
from moderation_groups import repeat_count


class GroupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.data=Path(self.tmp.name)
        self.store=ModerationIndex(self.data);self.at=1788967200000
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def event(self,kind='ban',delta=0,raw=None,**extra):
        return dict(kind=kind,user='someone',channel='one',at_ms=self.at+delta,
                    raw=raw or 'someone забанен.',source='chatterino',**extra)
    def add(self,e):
        with self.store.db:self.store.ingest(e)
    def test_localized_repeat_formats(self):
        for suffix in (' (×2) ',' (2 раза)',' (2 times)',' (х2)',' (x2)'):
            self.assertEqual(repeat_count('someone забанен.'+suffix),2)
        self.assertEqual(repeat_count('someone получил мут на 2с.'),1)
    def test_bans_two_three_times_are_one_event_and_restart_is_idempotent(self):
        events=[self.event(),self.event(delta=100,raw='someone забанен. (×2)'),
                self.event(delta=300,raw='someone забанен. (3 раза)')]
        for e in events:self.add(e)
        self.assertEqual(self.store.totals()['bans'],1)
        self.assertEqual(self.store.query()['rows'][0]['repeat_count'],3)
        self.store.close();self.store=ModerationIndex(self.data)
        for e in events:self.add(e)
        self.assertEqual(self.store.totals()['bans'],1)
    def test_timeout_update_preserves_context_and_uses_latest_duration(self):
        first=self.event('timeout',raw='someone получил мут на 10с.',duration=10)
        first['context']=[dict(user='someone',channel='one',id='one',text='before',time_utc='2026-09-09T14:59:59Z')]
        self.add(first)
        self.add(self.event('timeout',delta=500,raw='someone получил мут на 30с. (×2)',duration=30))
        result=self.store.query(now=self.at+30001)
        self.assertEqual(result['timeouts'],1)
        row=result['rows'][0];self.assertEqual(row['duration'],30);self.assertTrue(row['active'])
        self.assertEqual(row['context'][0]['text'],'before')
        self.assertFalse(self.store.query(now=self.at+30501)['rows'][0]['active'])
    def test_explicit_replacement_links_plain_notifications(self):
        self.add(self.event())
        old=self.store.query()['rows'][0]['key']
        self.add(self.event(delta=81,replaces_key=old))
        self.assertEqual(self.store.totals()['bans'],1)
    def test_new_punishments_other_channels_and_unban_remain_separate(self):
        self.add(self.event());old=self.store.query()['rows'][0]['key']
        self.add(self.event('unban',delta=100,raw='someone разбанен.'))
        self.add(self.event(delta=200,raw='someone забанен. (×2)',replaces_key=old))
        self.assertEqual(self.store.totals()['bans'],2)
        other=self.event(delta=201,raw='someone забанен. (×2)',replaces_key=old);other['channel']='two';self.add(other)
        self.add(self.event(delta=30000))
        self.assertEqual(self.store.totals()['bans'],4)
    def test_legacy_cache_is_regrouped_without_changing_originals(self):
        events=[self.event(),self.event(delta=500,raw='someone забанен. (×2)')]
        path=self.data/'moderation.jsonl';path.write_text(''.join(json.dumps(e)+'\n' for e in events),encoding='utf-8')
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        self.store.db.execute('PRAGMA user_version=0');self.store.db.commit();self.store.close()
        self.store=ModerationIndex(self.data);self.store.sync()
        self.assertEqual(self.store.totals()['bans'],1)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),digest)
        self.store.close();self.store=ModerationIndex(self.data);self.store.sync()
        self.assertEqual(self.store.totals()['bans'],1)
