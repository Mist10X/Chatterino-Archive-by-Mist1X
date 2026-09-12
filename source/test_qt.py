"""Real Qt event loop tests on disposable data; no production profile or Twitch."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import csv
import datetime as dt
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from PySide6.QtCore import Qt,QEvent,QPoint,QCoreApplication,QAbstractAnimation
from PySide6.QtGui import QFontInfo,QImage,QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from panel import App
from theme import install_style,Button,Theme
from storage import Control,atomic_write

qt=QApplication.instance() or QApplication([])
install_style(qt)

def wait_until(predicate,seconds=8):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        qt.processEvents()
        if predicate():return
        # Let Python worker threads run; qWait may retain the GIL inside Qt.
        time.sleep(.01)
    raise AssertionError('Timed out waiting for Qt/background result')

class QtTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.profile=Path(self.tmp.name)/'Профиль';(self.profile/'Settings').mkdir(parents=True)
        self.data=self.profile/'Plugins/history-tools/data';self.data.mkdir(parents=True)
        self.control=Control(self.data);self.control.users={'someone':True,'someone_else':False};self.control.channels=['one','two'];self.control.save()
        at=dt.datetime(2026,9,8,12,tzinfo=dt.timezone.utc)
        self.messages=[]
        for i in range(125):
            row={'user':'someone','channel':'one','id':str(i),'time_utc':(at+dt.timedelta(seconds=i)).isoformat(),'display_name':'Someone','text':f'Привет {i} <b>не HTML</b>'}
            if i==124:row['reply']={'state':'available','user':'quoted','display_name':'Quoted','channel':'one','text':'Исходное сообщение <img src="https://example.invalid/test">'}
            self.messages.append(row)
        self.archive=self.data/'someone--one.jsonl';atomic_write(self.archive,''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in self.messages));self.hash=hashlib.sha256(self.archive.read_bytes()).hexdigest()
        events=[dict(kind=kind,user=user,channel=ch,at_ms=int(at.timestamp()*1000)+200000+i,context=[self.messages[-1]] if ch=='one' and user=='someone' else [],raw='Системное событие',duration=600 if kind=='timeout' else None) for i,(kind,user,ch) in enumerate([('ban','someone','one'),('timeout','someone','two'),('ban','someone_else','one')])]
        atomic_write(self.data/'moderation.jsonl',''.join(json.dumps(e,ensure_ascii=False)+'\n' for e in events))
        from deletions import DeletionCapture
        capture=DeletionCapture(self.data/'twitch')
        capture.append(capture.event(['#one','Удалённый текст'],{'target-msg-id':'deleted1','login':'someone','tmi-sent-ts':str(int(at.timestamp()*1000)+200003)}))
        self.app=App(self.profile,True);self.app.show();wait_until(lambda:self.app.total==125)
    def tearDown(self):
        self.app.close();wait_until(lambda:not self.app.worker.is_alive());qt.processEvents();self.app.deleteLater();qt.processEvents();self.tmp.cleanup()
    def test_selection_reply_plain_text_and_export_all_pages(self):
        self.app.messages.setCurrentItem(self.app.messages.topLevelItem(0));self.app.show_message()
        self.assertIn('<img src=',self.app.preview.toPlainText());self.assertIn('<b>не HTML</b>',self.app.preview.toPlainText())
        self.assertNotIn('<b>',self.app.messages.topLevelItem(0).toolTip(2))
        old=self.app.messages.currentItem().data(0,Qt.UserRole)['id'];self.app.request_query()
        wait_until(lambda:self.app.worker.tasks.empty());QTest.qWait(500);self.app.poll()
        self.assertEqual(self.app.messages.selectedItems()[0].data(0,Qt.UserRole)['id'],old)
        destination=self.data/'export.csv';self.app.worker.tasks.put(('export',(destination,self.app.filters())))
        wait_until(lambda:destination.exists());wait_until(lambda:'Экспортировано' in self.app.detail.text())
        with destination.open(encoding='utf-8-sig',newline='') as f:rows=list(csv.reader(f))
        self.assertEqual(len(rows),126);self.assertTrue(any('Исходное' in str(r) for r in rows))
        self.app.page(1);wait_until(lambda:len(self.app.rows)==25);self.assertTrue(self.app.prev.isEnabled());self.assertFalse(self.app.next.isEnabled())
    def test_remove_pause_readd_and_archive_hash_unchanged(self):
        self.app.toggle_user();self.assertFalse(Control(self.data).users['someone'])
        self.app.remove_user();self.assertNotIn('someone',Control(self.data).users)
        self.assertEqual(hashlib.sha256(self.archive.read_bytes()).hexdigest(),self.hash)
        self.app.add_user('Someone');wait_until(lambda:self.app.selected=='someone' and self.app.total==125)
        self.assertTrue(Control(self.data).users['someone'])
    def test_exact_user_card_counts_context_and_filters(self):
        m=self.app.mod_view;m.open_user('someone');self.app.select_page(1)
        wait_until(lambda:m.total==3 and m.ban_count.text()=='1')
        self.assertEqual(m.total_caption.text(),'У пользователя:')
        self.assertEqual(m.mute_count.text(),'1');self.assertIn('баны: 2',m.global_counts.text())
        self.assertEqual(m.kind.currentText(),'Все события')
        self.assertEqual({row['kind'] for row in m.rows},{'ban','timeout','delete'})
        self.assertEqual([row['at_ms'] for row in m.rows],sorted([row['at_ms'] for row in m.rows],reverse=True))
        summaries={row['kind']:row.get('summary') for row in m.rows}
        self.assertEqual(summaries['delete'],'Удалённый текст')
        self.assertEqual(summaries['timeout'],'Сообщение недоступно')
        self.assertEqual(summaries['ban'],'Привет 124 <b>не HTML</b>')
        self.assertEqual(m.tree.headerItem().text(4),'Последнее сообщение')
        m.channel.setCurrentText('one');wait_until(lambda:m.total==2 and all(row['channel']=='one' for row in m.rows))
        self.assertEqual({row['kind'] for row in m.rows},{'ban','delete'})
        m.kind.setCurrentText('Удалённые сообщения');wait_until(lambda:m.total==1 and m.rows[0]['kind']=='delete')
        m.tree.setCurrentItem(m.tree.topLevelItem(0));m.show();self.assertIn('Удалённый текст',m.preview.toPlainText())
        m.kind.setCurrentText('Баны');m.channel.setCurrentText('one');wait_until(lambda:m.total==1 and m.rows[0]['kind']=='ban' and m.rows[0]['channel']=='one')
        self.assertEqual(m.ban_count.text(),'1');self.assertEqual(m.mute_count.text(),'1')
        m.tree.setCurrentItem(m.tree.topLevelItem(0));m.show();self.assertIn('Исходное сообщение',m.preview.toPlainText())
        m.reset();wait_until(lambda:m.total==3 and m.channel.currentText()=='Все каналы')
        self.assertEqual(m.kind.currentText(),'Все события')
        m.open_user('unknown');wait_until(lambda:m.title.text()=='@unknown' and m.total==0 and m.ban_count.text()=='0');self.assertEqual(m.ban_count.text(),'0')
        m.overview();wait_until(lambda:m.title.text()=='Модерация');self.assertEqual(m.total_caption.text(),'Всего в архиве:')
        self.assertEqual(m.total_caption.font().pointSizeF(),15)
        self.assertTrue(m.total_caption.alignment() & Qt.AlignTop)
    def test_narrow_layout_scroll_focus_and_scaled_fonts(self):
        self.app.resize(640,420);QTest.qWait(120)
        self.assertEqual(self.app.width(),640);self.assertEqual(self.app.height(),420)
        trim=self.app.responsive.quick_trim;point=trim.mapTo(self.app,QPoint(0,0))
        self.assertGreaterEqual(point.x(),0);self.assertLessEqual(point.x()+trim.width(),640);self.assertLess(point.y()+trim.height(),350)
        page=self.app.pages.widget(0);self.assertEqual(page.horizontalScrollBar().maximum(),0);self.assertGreater(page.verticalScrollBar().maximum(),0)
        self.app.export_button.setFocus();QTest.qWait(80)
        self.assertEqual(page.horizontalScrollBar().value(),0);self.assertGreater(page.verticalScrollBar().value(),0)
        self.assertEqual(QFontInfo(self.app.messages.font()).family(),'Archive Montserrat')
        self.assertGreaterEqual(self.app.messages.font().pointSizeF(),10.5)
    def test_hover_shrinks_visual_only_recovers_and_keyboard_works(self):
        b=self.app.history_button;geometry=b.geometry();before=b.visual_rect();called=[]
        b.clicked.connect(lambda:called.append(True))
        b.animate(True);QTest.qWait(210)
        self.assertLess(b.visual_rect().width(),before.width());self.assertEqual(b.geometry(),geometry)
        b.setFocus();QTest.keyClick(b,Qt.Key_Space);self.assertEqual(len(called),1)
        b.animate(False);QTest.qWait(210);self.assertAlmostEqual(b.visual_rect().width(),before.width(),places=3)
        b.animate(True);QTest.qWait(30);self.app.theme.set_motion(False)
        self.assertEqual(b.anim.state(),QAbstractAnimation.Stopped);self.assertAlmostEqual(b.visual_rect().width(),before.width(),places=3)
        b.setEnabled(False);QTest.keyClick(b,Qt.Key_Space);self.assertEqual(len(called),1)
        self.assertFalse(json.loads((self.data/'panel-ui.json').read_text())['animations'])
    def test_trim_acknowledgement_displays_plugin_detail(self):
        token=self.app.control.request_trim('one')
        self.assertIsNotNone(self.app.control.pending_request())
        atomic_write(self.data/'panel-ack.json',json.dumps({'id':token,'status':'ok','detail':'Оставлено 1000. Архив сохранён.'},ensure_ascii=False))
        self.app.poll();self.assertIsNone(self.app.control.pending_request())
        self.assertEqual(self.app.detail.text(),'Оставлено 1000. Архив сохранён.')
        self.assertEqual(hashlib.sha256(self.archive.read_bytes()).hexdigest(),self.hash)

if __name__=='__main__':unittest.main()
