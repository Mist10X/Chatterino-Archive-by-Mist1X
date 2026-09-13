import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json
import tempfile
from pathlib import Path
import unittest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from panel import App
from deletions import DeletionCapture
import test_qt

qt=QApplication.instance() or QApplication([])

class DeletedCardTests(unittest.TestCase):
    def test_all_and_individual_filters_personal_counter_and_deleted_text_card(self):
        with tempfile.TemporaryDirectory() as temporary:
            profile=Path(temporary);(profile/'Settings').mkdir();data=profile/'Plugins/history-tools/data'
            capture=DeletionCapture(data/'twitch')
            capture.append(capture.event(['#one','<b>исходный текст</b>'],{'login':'someone','target-msg-id':'one','tmi-sent-ts':'1788868801000'}))
            window=App(profile,demo=True);window.show();m=window.mod_view
            try:
                self.assertEqual([m.kind.itemText(i) for i in range(m.kind.count())],['Все события','Муты','Баны','Удалённые сообщения','Размуты'])
                m.kind.setCurrentText('Удалённые сообщения');m.open_user('someone')
                test_qt.wait_until(lambda:m.total==1 and m.delete_count.text()=='1')
                self.assertEqual(m.ban_count.text(),'0');self.assertEqual(m.mute_count.text(),'0')
                m.tree.setCurrentItem(m.tree.topLevelItem(0));m.show()
                self.assertIn('<b>исходный текст</b>',m.preview.toPlainText());self.assertIn('УДАЛЕНО СООБЩЕНИЕ',m.preview.toPlainText())
                m.kind.setCurrentText('Баны');test_qt.wait_until(lambda:m.total==0 and m.delete_count.text()=='1')
                self.assertEqual(m.delete_count.text(),'1')
                m.kind.set_values(('timeout','delete'));test_qt.wait_until(lambda:m.total==1 and m.rows[0]['kind']=='delete')
                self.assertEqual(m.kind.currentText(),'Муты, Удалённые сообщения')
                self.assertTrue(m.channel.isEditable());self.assertEqual(m.channel.completer().filterMode(),Qt.MatchContains)
                item=m.tree.topLevelItem(0);item.setData(0,Qt.UserRole,{'kind':'reward_unmute','user':'someone','channel':'one','at_ms':1788868801000,'status':'Размут','summary':'Размут купил buyer'})
                m.app.responsive.reformat();self.assertIn('Размут',item.text(3))
            finally:
                window.exit_app();test_qt.wait_until(lambda:not window.worker.is_alive());qt.processEvents();window.deleteLater();qt.processEvents()
