import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from storage import ArchiveIndex
from moderation_store import ModerationIndex


class Revision04Tests(unittest.TestCase):
    def test_reply_enriches_old_index_without_duplicates_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp);cache=data/'panel-cache';cache.mkdir()
            db=sqlite3.connect(cache/'archive.sqlite3')
            db.execute('CREATE TABLE messages(user TEXT,channel TEXT,id TEXT,time_utc TEXT,display_name TEXT,text TEXT,source TEXT,PRIMARY KEY(user,channel,id))')
            db.commit();db.close()
            path=data/'someone--channel.jsonl'
            item={'user':'someone','channel':'channel','id':'one','text':'Answer','time_utc':'2026-09-08T12:00:00Z'}
            path.write_text(json.dumps(item)+'\n',encoding='utf-8')
            index=ArchiveIndex(data);index.sync()
            self.assertIsNone(index.query('someone')[1][0]['reply'])
            item['reply']={'state':'available','channel':'channel','user':'other','display_name':'Other','text':'=Quoted original'}
            with path.open('a',encoding='utf-8') as f:f.write(json.dumps(item)+'\n')
            index.sync();index.close();index=ArchiveIndex(data);index.sync()
            total,rows=index.query('someone')
            self.assertEqual(total,1);self.assertEqual(rows[0]['text'],'Answer')
            self.assertEqual(rows[0]['reply']['text'],'=Quoted original')
            index.export(data/'export.csv','someone');index.close()
            with (data/'export.csv').open(encoding='utf-8-sig',newline='') as f:export=list(csv.reader(f))
            self.assertEqual(export[1][-2:],["Other","'=Quoted original"])

    def test_personal_card_is_exact_and_counts_separate_from_global_and_channel(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp);index=ModerationIndex(data)
            with index.db:
                for i,(user,channel,kind) in enumerate([('someone','one','ban'),('someone','two','timeout'),('someone_else','one','ban')]):
                    index.ingest(dict(kind=kind,user=user,channel=channel,at_ms=1788868800000+i,context=[]))
            self.assertEqual(index.totals(),{'bans':2,'timeouts':1,'deletions':0})
            self.assertEqual(index.totals('someone'),{'bans':1,'timeouts':1,'deletions':0})
            self.assertEqual(index.query('someone',exact_user=True)['total'],2)
            self.assertEqual(index.query('someone','one',exact_user=True)['total'],1)
            self.assertEqual(index.users('SOME')['total'],2)
            self.assertEqual(index.users('zero',extra=['zero'])['rows'][0]['bans'],0)
            self.assertEqual(index.query('never_seen',exact_user=True)['total'],0)
            index.close()
