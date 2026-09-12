import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json
from pathlib import Path
import tempfile
import unittest
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from panel import App
from twitch_dialog import TwitchDialog
from twitch_core import settings_load
from storage import Control

qt=QApplication.instance() or QApplication([])


class TwitchQtTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.profile=Path(self.tmp.name)/'Profile';(self.profile/'Settings').mkdir(parents=True)
        (self.profile/'Settings/window-layout.json').write_text(json.dumps({'tabs':[{'type':'twitch','name':'morphe_ya'}]}))
        self.app=App(self.profile,demo=True);self.app.show();qt.processEvents()
    def tearDown(self):
        self.app.exit_app()
        for _ in range(80):
            qt.processEvents();QTest.qWait(25)
            if not self.app.worker.is_alive():break
        qt.processEvents();self.app.deleteLater();qt.processEvents();self.tmp.cleanup()
    def test_cancel_and_save_independent_channels(self):
        before=list(self.app.control.channels);d=TwitchDialog(self.app)
        d.channels.setPlainText('dangerlyoha');d.enabled.setChecked(True);d.reject()
        self.assertFalse(settings_load(self.app.data/'twitch')['enabled'])
        d=TwitchDialog(self.app);d.channels.setPlainText('dangerlyoha');d.enabled.setChecked(True);d.save()
        self.assertEqual(settings_load(self.app.data/'twitch')['channels'],['dangerlyoha'])
        self.assertTrue(self.app.twitch_config['enabled']);self.assertEqual(self.app.control.channels,before)
        self.assertIn('dangerlyoha',[self.app.channel.itemText(i) for i in range(self.app.channel.count())])
    def test_import_and_invalid_settings_preserve_original(self):
        d=TwitchDialog(self.app);d.channels.setPlainText('dangerlyoha');d.import_channels()
        self.assertEqual(set(d.channels.toPlainText().splitlines()),{'morphe_ya','dangerlyoha'})
        d.channels.setPlainText('../bad');d.save();self.assertTrue(d.error.text());self.assertEqual(self.app.twitch_config['channels'],[])
    def test_tray_close_keeps_worker_and_explicit_exit_stops(self):
        class Tray:
            def showMessage(self,*args):pass
            def hide(self):pass
        self.app.tray=Tray();self.app.close();qt.processEvents()
        self.assertFalse(self.app.isVisible());self.assertTrue(self.app.worker.is_alive());self.assertFalse(self.app.closing)
        self.app.restore_window();qt.processEvents();self.assertTrue(self.app.isVisible())

    def test_auth_controls_scroll_without_overlapping_actions(self):
        self.app.twitch.report(phase='auth',auth_url='https://www.twitch.tv/activate',auth_code='TEST')
        d=TwitchDialog(self.app);d.resize(590,600);d.show();QTest.qWait(100)
        self.assertGreater(d.body.verticalScrollBar().maximum(),0)
        self.assertGreaterEqual(d.channels.height(),150)
        self.assertLessEqual(d.body.geometry().bottom(),d.height()-60)
        d.reject()
