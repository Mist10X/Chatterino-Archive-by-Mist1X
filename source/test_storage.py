import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from storage import ArchiveIndex, Control, atomic_write, layout_channels, name


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.path = self.data / 'mist1x_x--morphe_ya.jsonl'
        self.index = ArchiveIndex(self.data)

    def tearDown(self):
        self.index.close()
        self.tmp.cleanup()

    def record(self, id='a', user='Mist1X_X', channel='morphe_ya', text='Привет чат', **extra):
        return json.dumps(dict(id=id, user=user, channel=channel, text=text,
                               display_name=user, time_utc='2026-09-08T12:00:00.000Z', **extra), ensure_ascii=False)

    def test_incremental_partial_tail_duplicate_and_restart(self):
        raw = self.record()
        self.path.write_text(raw + '\n' + raw + '\n' + self.record('b')[:35], encoding='utf-8')
        self.assertEqual(self.index.sync(), 1)
        self.assertEqual(self.index.sync(), 0)
        with self.path.open('a', encoding='utf-8') as f:
            f.write(self.record('b')[35:] + '\n')
        self.assertEqual(self.index.sync(), 1)
        self.index.close()
        self.index = ArchiveIndex(self.data)
        self.assertEqual(self.index.sync(), 0)
        self.assertEqual(self.index.query('mist1x_x')[0], 2)

    def test_channels_users_and_cyrillic_search(self):
        self.path.write_text(self.record() + '\n', encoding='utf-8')
        other = self.data / 'mist1x_x--dangerlyoha.jsonl'
        other.write_text(self.record('b', channel='dangerlyoha', text='Другой КАНАЛ') + '\n', encoding='utf-8')
        wrong = self.data / 'someone--dangerlyoha.jsonl'
        wrong.write_text(self.record('c', user='someone', channel='dangerlyoha') + '\n', encoding='utf-8')
        self.index.sync()
        self.assertEqual(self.index.query('mist1x_x')[0], 2)
        self.assertEqual(self.index.query('mist1x_x', 'morphe_ya')[0], 1)
        self.assertEqual(self.index.query('mist1x_x', search='другой канал')[0], 1)
        self.assertEqual(self.index.query('someone')[0], 1)
        self.assertEqual(self.index.query('mist1x_x', search="' OR 1=1 --")[0], 0)

    def test_bad_record_does_not_erase_original_or_cross_attribute(self):
        text = '{invalid}\n' + self.record(channel='another') + '\n' + self.record() + '\n'
        self.path.write_text(text, encoding='utf-8')
        self.index.sync()
        self.assertEqual(self.index.invalid_count(), 2)
        self.assertEqual(self.index.query('mist1x_x')[0], 1)
        self.assertEqual(self.path.read_text(encoding='utf-8'), text)

    def test_state_migration_and_atomic_roundtrip(self):
        self.path.write_text(self.record() + '\n', encoding='utf-8')
        self.path.with_suffix('.state').write_text('on\noff\non\n')
        c = Control(self.data)
        self.assertTrue(c.users['mist1x_x'])
        c.users['second_user'] = False
        c.channels.append('dangerlyoha')
        c.save()
        restored = Control(self.data)
        self.assertEqual(c.users, restored.users)
        self.assertEqual(c.channels, restored.channels)
        self.assertEqual(restored.keep, 1000)
        self.assertEqual(self.path.with_suffix('.state').read_text(), 'on\noff\non\n')

    def test_invalid_config_and_path_traversal(self):
        for value in ['../../foo', 'C:\\test', 'a\nb', 'a\ton', '', 'а']:
            with self.assertRaises(ValueError):
                name(value)
        self.assertEqual(name(' @Mist1X_X '), 'mist1x_x')
        atomic_write(self.data / 'panel-control.txt', 'HT2\nrevision\tx\nuser\tfoo\ton\n')
        with self.assertRaises(ValueError):
            Control(self.data)

    def test_request_requires_ack_before_next_request(self):
        c = Control(self.data)
        token = c.request_trim('morphe_ya')
        self.assertEqual(c.pending_request()[1], token)
        with self.assertRaises(ValueError):
            c.request_trim('dangerlyoha')
        atomic_write(self.data / 'panel-ack.json', json.dumps({'id':token, 'status':'ok'}))
        self.assertIsNone(c.pending_request())
        self.assertNotEqual(c.request_trim('dangerlyoha'), token)

    def test_nested_layout_only_twitch_channels(self):
        settings = self.data / 'Settings'
        settings.mkdir()
        atomic_write(settings / 'window-layout.json', json.dumps({'windows':[{'tabs':[
            {'splits2':{'children':[{'data':{'type':'twitch','name':'Morphe_ya'}},
                                   {'data':{'type':'twitch','name':'dangerlyoha'}},
                                   {'data':{'type':'mentions','name':'mentions'}}]}}]}]}))
        self.assertEqual(layout_channels(self.data), ['dangerlyoha','morphe_ya'])

    def test_paging_date_and_export_entire_selection(self):
        self.path.write_text(''.join(self.record(str(i), text='=1+1') + '\n' for i in range(205)), encoding='utf-8')
        self.index.sync()
        self.assertEqual(len(self.index.query('mist1x_x')[1]), 100)
        self.assertEqual(len(self.index.query('mist1x_x', offset=200)[1]), 5)
        self.assertEqual(self.index.query('mist1x_x', day='2026-09-08')[0], 205)
        self.assertEqual(self.index.query('mist1x_x', day='2026-09-09')[0], 0)
        destination = self.data / 'export.csv'
        self.assertEqual(self.index.export(destination, 'mist1x_x'), 205)
        self.assertIn("'=1+1", destination.read_text(encoding='utf-8-sig'))

    def test_old_index_re_reads_direct_twitch_ids_once(self):
        direct=self.data/'twitch/messages/mist1x_x--morphe_ya.jsonl';direct.parent.mkdir(parents=True)
        direct.write_text(self.record(user_id='42')+'\n',encoding='utf-8')
        self.index.close()
        cache=self.data/'panel-cache/archive.sqlite3'
        for suffix in ('','-wal','-shm'):Path(str(cache)+suffix).unlink(missing_ok=True)
        db=sqlite3.connect(cache)
        db.executescript('''CREATE TABLE messages(user TEXT,channel TEXT,id TEXT,time_utc TEXT,display_name TEXT,text TEXT,source TEXT,reply TEXT,PRIMARY KEY(user,channel,id));
            CREATE TABLE files(path TEXT PRIMARY KEY,offset INTEGER,invalid INTEGER DEFAULT 0);''')
        db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)',('mist1x_x','morphe_ya','a','2026-09-08T12:00:00.000Z','Mist1X_X','Привет чат','plugin',None))
        db.execute('INSERT INTO files VALUES(?,?,0)',('twitch/messages/'+direct.name,direct.stat().st_size));db.commit();db.close()
        self.index=ArchiveIndex(self.data);self.index.sync()
        self.assertEqual(self.index.query('mist1x_x')[1][0]['user_id'],'42')


if __name__ == '__main__':
    unittest.main(verbosity=2)
