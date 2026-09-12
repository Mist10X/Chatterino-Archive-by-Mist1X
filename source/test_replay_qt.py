import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
from pathlib import Path
import unittest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QImage,QColor
from panel import App
from replay_store import Session
from replay_view import ReplayWindow
from test_replay import stream,message,BASE
from test_qt import qt,wait_until

class ReplayQtTests(unittest.TestCase):
    def test_replay_seek_moderation_search_context_playback_and_narrow_window(self):
        with tempfile.TemporaryDirectory() as temp:
            profile=Path(temp);(profile/'Settings').mkdir();data=profile/'Plugins/history-tools/data'
            s=Session(data,stream(),BASE)
            for i in range(1,310):s.append(message(i))
            s.append({'kind':'timeout','at':BASE+5000,'channel':'morphe_ya','user':'someone','duration':10});s.flush();meta=dict(s.meta);s.close()
            app=App(profile,demo=True);app.show();w=ReplayWindow(app,meta);w.resize(600,700);w.show()
            try:
                wait_until(lambda:not w.busy and bool(w.rows))
                self.assertEqual(app.replays.config['channels'],[])
                w.seek(BASE+4000);wait_until(lambda:not w.busy);self.assertEqual(w.dim,set())
                w.seek(BASE+6000);wait_until(lambda:not w.busy);self.assertTrue(w.dim);self.assertIn('получил мут на 10 с.',w.chat.toPlainText())
                w.shade.setChecked(False);self.assertNotIn('получил мут',w.chat.toPlainText());w.shade.setChecked(True)
                w.seek(BASE+4000);wait_until(lambda:not w.busy);self.assertFalse(w.dim)
                w.toggle_play();QTest.qWait(1300);qt.processEvents();self.assertGreater(w.current,BASE+5000);w.toggle_play()
                w.request('context',BASE+100000,100);wait_until(lambda:not w.busy);self.assertEqual(w.chat.highlight_id,100);self.assertIn('Сообщение 100',w.chat.toPlainText())
                self.assertTrue(w.chat.viewport().height()>150)
                self.assertLessEqual(w.search.mapTo(w,w.search.rect().bottomRight()).x(),w.width())
                ref={'key':'a'*64,'file':'a'*64+'.png','animated':False}
                self.assertIsNone(w.media.image(ref));arrived=[];w.media.changed.connect(lambda:arrived.append(True))
                assets=s.folder/'assets';assets.mkdir(exist_ok=True)
                image=QImage(40,40,QImage.Format_ARGB32);image.fill(QColor('#b090df'));image.save(str(assets/ref['file']))
                wait_until(lambda:bool(arrived));self.assertFalse(w.media.image(ref).isNull())
            finally:
                w.close();app.exit_app();wait_until(lambda:not app.worker.is_alive());qt.processEvents();app.deleteLater();qt.processEvents()
