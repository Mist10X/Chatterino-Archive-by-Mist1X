import json,tempfile,unittest
from contextlib import closing
from pathlib import Path
from panel import App
from reward_dialog import RewardDialog
from test_qt import qt,wait_until
from moderation_evidence import EvidenceWriter,rules_load

class RewardQtTests(unittest.TestCase):
    def test_observed_reward_can_be_selected_saved_and_reopened(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile=Path(tmp);(profile/'Settings').mkdir();data=profile/'Plugins/history-tools/data'
            EvidenceWriter(data).append({'kind':'reward','id':'sample','channel':'samplechannel','buyer':'someone','reward':'reward-id','input':'target','at_ms':1789200000000,'status':'observed'})
            app=App(profile,demo=True,network=False);app.twitch_config['channels']=['samplechannel'];app.show()
            dialog=None
            try:
                wait_until(lambda:(data/'panel-cache/moderation.sqlite3').exists())
                import sqlite3,time
                def ready():
                    with closing(sqlite3.connect(data/'panel-cache/moderation.sqlite3')) as db:
                        try:return db.execute('SELECT count(*) FROM evidence').fetchone()[0]==1
                        except sqlite3.OperationalError:return False
                wait_until(ready)
                dialog=RewardDialog(app);dialog.show();qt.processEvents()
                self.assertEqual(dialog.reward.currentData(),'reward-id')
                dialog.bot.setText('trustedbot');dialog.template.setText('{buyer} muted {target}');dialog.save()
                self.assertEqual(rules_load(data)[0]['reward'],'reward-id')
                dialog.close();dialog=RewardDialog(app);dialog.saved.setCurrentRow(0)
                self.assertEqual(dialog.bot.text(),'trustedbot');self.assertEqual(dialog.reward.currentData(),'reward-id')
            finally:
                if dialog:dialog.close()
                app.exit_app();wait_until(lambda:not app.worker.is_alive() and not app.shared.is_alive());qt.processEvents();app.deleteLater();qt.processEvents()
