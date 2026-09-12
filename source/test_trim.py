import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import json,tempfile,unittest
from types import SimpleNamespace
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication,QWidget
from storage import Control,atomic_write
from theme import Theme,label,install_style
from trim_dialog import TrimDialog

qt=QApplication.instance() or QApplication([]);install_style(qt)
class TrimTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.data=Path(self.tmp.name)
        self.app=QWidget();self.app.control=Control(self.data);self.app.theme=Theme(self.data);self.app.detail=label()
        self.app.safe=lambda fn:fn();self.status(['one','two']);self.dialog=None
    def status(self,channels):
        atomic_write(self.data/'panel-status.json',json.dumps({'version':'0.8.0','trim_except':True,'discovery':'open_tabs','channels':[{'name':ch,'attached':True,'open':True} for ch in channels]}))
    def tearDown(self):
        if self.dialog:self.dialog.close();self.dialog.deleteLater()
        self.app.close();self.app.deleteLater();qt.processEvents();self.tmp.cleanup()
    def create(self):self.dialog=TrimDialog(self.app);self.dialog.show();qt.processEvents();return self.dialog
    def test_preview_and_exclusions_survive_reopen_without_clearing(self):
        d=self.create();d.choices.item(0).setCheckState(Qt.Checked);d.save_only()
        self.assertFalse((self.data/'panel-request.txt').exists())
        self.app.theme=Theme(self.data);d=self.create()
        self.assertEqual(d.excluded(),{'one'});self.assertIn('#one',d.summary.toPlainText());d.submit()
        body=(self.data/'panel-request.txt').read_text();self.assertIn('exclude\tone\n',body);self.assertNotIn('exclude\ttwo',body)
    def test_all_excluded_disables_clear_and_cancel_does_not_save(self):
        d=self.create()
        for i in range(d.choices.count()):d.choices.item(i).setCheckState(Qt.Checked)
        self.assertFalse(d.confirm.isEnabled());d.reject()
        self.assertFalse((self.data/'panel-request.txt').exists());self.assertFalse((self.data/'panel-ui.json').exists())
    def test_changed_channels_or_old_plugin_do_not_send_a_request(self):
        d=self.create();self.status(['one','two','new'])
        with self.assertRaises(ValueError):d.submit()
        self.assertFalse((self.data/'panel-request.txt').exists())
        atomic_write(self.data/'panel-status.json',json.dumps({'channels':[]}))
        with self.assertRaises(ValueError):TrimDialog(self.app)
    def test_closed_exclusion_is_remembered_and_processing_blocks_second_request(self):
        self.app.theme.settings['trim_excluded_channels']=['closed'];self.app.theme.save();d=self.create()
        self.assertIn('closed',d.excluded());d.submit();body=(self.data/'panel-request.txt').read_text();self.assertNotIn('exclude\tclosed',body)
        token=self.app.control.pending_request()[1];atomic_write(self.data/'panel-ack.json',json.dumps({'id':token,'status':'processing'}))
        with self.assertRaises(ValueError):self.app.control.request_trim_except(['one','two'],[])
        atomic_write(self.data/'panel-ack.json',json.dumps({'id':token,'status':'ok'}));self.assertIsNone(self.app.control.pending_request())

if __name__=='__main__':unittest.main()
