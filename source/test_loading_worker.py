"""Queue pressure, bounded indexing, and continuous ingestion regressions."""
import json
import queue
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from panel_worker import PendingTasks, Worker
from storage import ArchiveIndex, Control
from moderation_store import ModerationIndex
from test_loading_qt import message
from test_qt import qt, wait_until
from panel import App
from PySide6.QtTest import QTest


class WorkerLoadingTests(unittest.TestCase):
    def test_replaces_obsolete_queries_but_preserves_every_export(self):
        tasks=PendingTasks()
        for i in range(1000):
            tasks.put(('query',(i,('alpha','','',''),0)))
            tasks.put(('moderation',(i,('','',''),0,'',())))
            if i%100==0:tasks.put(('export',(str(i),('alpha','','',''))))
        self.assertEqual(tasks.qsize(),12)
        items=[]
        while not tasks.empty():items.append(tasks.get_nowait());tasks.task_done()
        self.assertEqual([p[0] for kind,p in items if kind=='query'],[999])
        self.assertEqual([p[0] for kind,p in items if kind=='moderation'],[999])
        self.assertEqual([p[0] for kind,p in items if kind=='export'],[str(i) for i in range(0,1000,100)])
        self.assertEqual(tasks.unfinished_tasks,0)

    def test_bounded_sync_is_fair_and_unchanged_files_do_not_write(self):
        with tempfile.TemporaryDirectory() as folder:
            data=Path(folder)
            for user in ('alpha','beta','gamma'):
                (data/(user+'--one.jsonl')).write_text(''.join(json.dumps(message(user,str(i)))+'\n' for i in range(130)),encoding='utf-8')
            index=ArchiveIndex(data)
            try:
                for _ in range(3):index.sync(max_seconds=0)
                self.assertEqual(index.counts(),dict(alpha=32,beta=32,gamma=32))
                for _ in range(12):index.sync(max_seconds=0)
                self.assertEqual(index.counts(),dict(alpha=130,beta=130,gamma=130))
                before=index.db.total_changes;index.sync();self.assertEqual(index.db.total_changes,before)
            finally:index.close()
            (data/'moderation.jsonl').write_text(''.join(json.dumps(dict(kind='timeout',user='alpha',channel='one',at_ms=1788955200000+i*100000,duration=30))+'\n' for i in range(130)),encoding='utf-8')
            moderation=ModerationIndex(data)
            try:
                for _ in range(6):moderation.sync(max_seconds=0)
                self.assertEqual(moderation.totals()['timeouts'],130)
                before=moderation.db.total_changes;moderation.sync();self.assertEqual(moderation.db.total_changes,before)
            finally:moderation.close()

    def test_query_failure_recovers_and_export_survives_queue_pressure(self):
        with tempfile.TemporaryDirectory() as folder:
            data=Path(folder)
            (data/'alpha--one.jsonl').write_text(json.dumps(message('alpha'))+'\n',encoding='utf-8')
            original=ArchiveIndex.query
            failed=threading.Event()
            def query(index,*args,**kwargs):
                if not failed.is_set():failed.set();raise sqlite3.OperationalError('temporary lock')
                return original(index,*args,**kwargs)
            worker=Worker(data)
            with patch.object(ArchiveIndex,'query',query):
                worker.start();worker.tasks.put(('query',(1,('alpha','','',''),0)))
                seen=[];deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    item=worker.results.get(timeout=2);seen.append(item)
                    if item[0]=='query_error':break
                self.assertEqual(seen[-1][0],'query_error')
                destination=data/'export.csv'
                worker.tasks.put(('export',(destination,('alpha','','',''))))
                for token in range(2,102):worker.tasks.put(('query',(token,('alpha','','',''),0)))
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    item=worker.results.get(timeout=2);seen.append(item)
                    if item[0]=='query' and item[1][0]==101:break
                worker.stopped.set();worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertTrue(destination.exists())
            self.assertTrue(any(kind=='query' and payload[0]==101 and payload[1]==1 for kind,payload in seen))

    def test_fast_switching_displays_messages_while_recording_continues(self):
        with tempfile.TemporaryDirectory() as folder:
            profile=Path(folder);data=profile/'Plugins/history-tools/data';data.mkdir(parents=True)
            control=Control(data);control.users={'alpha':True,'beta':True};control.channels=['one'];control.save()
            for user in control.users:(data/(user+'--one.jsonl')).write_text(json.dumps(message(user))+'\n',encoding='utf-8')
            stopped=threading.Event();written=[]
            def produce():
                for i in range(2,500):
                    if stopped.wait(.04):return
                    for user in ('alpha','beta'):
                        with (data/(user+'--one.jsonl')).open('a',encoding='utf-8') as f:f.write(json.dumps(message(user,str(i)))+'\n')
                    written.append(i)
            original=ArchiveIndex.query
            def slow_query(index,*args,**kwargs):
                time.sleep(.24)
                return original(index,*args,**kwargs)
            producer=threading.Thread(target=produce,daemon=True)
            with patch.object(ArchiveIndex,'query',slow_query):
                app=App(profile,demo=True);producer.start()
                try:
                    wait_until(lambda:app.total>0)
                    for i in range(30):
                        app.choose_user('beta' if i%2 else 'alpha');QTest.qWait(12)
                        self.assertLessEqual(app.worker.tasks.qsize(),2)
                    app.choose_user('beta');start=time.monotonic()
                    wait_until(lambda:app.rows and all(row['user']=='beta' for row in app.rows),seconds=3)
                    self.assertLess(time.monotonic()-start,3)
                    first_total=app.total;wait_until(lambda:app.total>first_total,seconds=3)
                    self.assertTrue(producer.is_alive())
                    self.assertTrue(written)
                finally:
                    stopped.set();producer.join(2)
                    app.close();wait_until(lambda:not app.worker.is_alive());app.deleteLater();qt.processEvents()


if __name__=='__main__':unittest.main()
