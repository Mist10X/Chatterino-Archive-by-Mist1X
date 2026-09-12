import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json
import tempfile
from pathlib import Path
import unittest
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
                self.assertEqual([m.kind.itemText(i) for i in range(m.kind.count())],['Все события','Муты','Баны','Удалённые сообщения'])
                m.kind.setCurrentText('Удалённые сообщения');m.open_user('someone')
                test_qt.wait_until(lambda:m.total==1 and m.delete_count.text()=='1')
                self.assertEqual(m.ban_count.text(),'0');self.assertEqual(m.mute_count.text(),'0')
                m.tree.setCurrentItem(m.tree.topLevelItem(0));m.show()
                self.assertIn('<b>исходный текст</b>',m.preview.toPlainText());self.assertIn('УДАЛЕНО СООБЩЕНИЕ',m.preview.toPlainText())
                m.kind.setCurrentText('Баны');test_qt.wait_until(lambda:m.total==0 and m.delete_count.text()=='1')
                self.assertEqual(m.delete_count.text(),'1')
            finally:
                window.exit_app();test_qt.wait_until(lambda:not window.worker.is_alive());qt.processEvents();window.deleteLater();qt.processEvents()
