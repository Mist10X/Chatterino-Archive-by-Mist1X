import os,tempfile,time,unittest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from unittest.mock import patch
from connection_alerts import connection_warning
from test_qt import qt,wait_until
from panel import App
from theme import Dialog
from replay_view import ReplayWindow
from replay_store import Session
from test_replay import stream,message,BASE
from test_user_history import card
from moderation_store import ModerationIndex
from shared_bans import Store

class AlertTests(unittest.TestCase):
    def test_states(self):
        config={'enabled':True};wanted=['one','two']
        self.assertIsNone(connection_warning({'phase':'connected','joined':wanted},config,wanted))
        self.assertEqual(connection_warning({'phase':'auth'},config,wanted)[0],'error')
        self.assertEqual(connection_warning({'phase':'reconnecting'},config,wanted)[0],'error')
        self.assertEqual(connection_warning({'phase':'connected','joined':['one']},config,wanted)[0],'warning')
        self.assertEqual(connection_warning({}, {'enabled':False},wanted)[0],'off')
    def test_every_page_dialog_and_paused_replay_late_context(self):
        with tempfile.TemporaryDirectory() as folder:
            profile=Path(folder);(profile/'Settings').mkdir();data=profile/'Plugins/history-tools/data'
            s=Session(data,stream(),BASE);s.append(message(1));s.append(dict(kind='ban',user='someone',channel='morphe_ya',at=BASE+2000));s.flush();meta=dict(s.meta);s.close()
            app=App(profile,demo=True);app.demo=False;app.resize(760,800);app.show()
            w=ReplayWindow(app,meta);w.resize(600,700);w.show()
            dialog=Dialog(app,'Проверка','Тест предупреждения');dialog.resize(500,300);dialog.show()
            try:
                app.twitch_config['enabled']=True
                with patch.object(app.twitch,'snapshot',return_value={'phase':'auth'}):
                    app.connection_alerts.refresh();qt.processEvents()
                    for page in range(4):app.select_page(page);self.assertTrue(app.connection_banner.isVisible())
                    self.assertTrue(w.connection_banner.isVisible());self.assertTrue(dialog.connection_banner.isVisible())
                    self.assertLessEqual(w.connection_banner.width(),w.width())
                    self.assertFalse(app.connection_retry.isVisible())
                reconnecting={'phase':'reconnecting','text':'Повторяем','saved':0,'deleted':0,'joined':[]}
                with patch.object(app.twitch,'snapshot',return_value=reconnecting),patch.object(app.twitch,'command') as command:
                    app.connection_alerts.refresh();qt.processEvents();self.assertTrue(app.connection_retry.isVisible())
                    command.reset_mock();app.connection_retry.click();command.assert_called_once_with('retry')
                with patch.object(app.twitch,'snapshot',return_value={'phase':'connected','joined':app.twitch.wanted_channels()}):
                    app.connection_alerts.refresh();self.assertFalse(app.connection_banner.isVisible());self.assertFalse(dialog.connection_banner.isVisible())
                wait_until(lambda:not w.busy and len(w.rows)==2)
                index=ModerationIndex(data)
                try:
                    with index.db:index.ingest(dict(kind='ban',user='someone',channel='morphe_ya',at_ms=BASE+2000,context=[],source='chatterino'))
                    Store(data).save(card(channel='morphe_ya',at=BASE+2000))
                    index.combined.next_scan=0;index.sync()
                finally:index.close()
                app.archive_context_changed.emit()
                wait_until(lambda:'AutoMod' in w.chat.toPlainText())
                self.assertIn('Задержано <b>не HTML</b>',w.chat.toPlainText());self.assertFalse(w.playing)
                self.assertEqual(w.chat.document().blockCount(),2)
            finally:
                dialog.close();w.close();app.exit_app();wait_until(lambda:not app.worker.is_alive() and not app.shared.is_alive());qt.processEvents();app.deleteLater();qt.processEvents()

if __name__=='__main__':unittest.main()
