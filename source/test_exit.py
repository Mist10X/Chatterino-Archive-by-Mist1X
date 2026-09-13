import os,subprocess,sys,unittest
from pathlib import Path

class ExitTests(unittest.TestCase):
    def test_show_request_restores_hidden_window(self):
        code='''
import json,tempfile,time
from pathlib import Path
from PySide6.QtWidgets import QApplication
from panel import App
app=QApplication([])
with tempfile.TemporaryDirectory() as tmp:
    profile=Path(tmp);(profile/'Settings').mkdir();window=App(profile,demo=True,network=False);window.show();window.hide()
    request=window.data/'panel-show-request.json';request.write_text(json.dumps({'action':'show','requested_at':time.time()}))
    window.poll();app.processEvents();assert window.isVisible();assert not request.exists();window.exit_app()
    while window.worker.is_alive():app.processEvents()
'''
        result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).parent,env=dict(os.environ,QT_QPA_PLATFORM='offscreen'),capture_output=True,text=True,timeout=12)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_explicit_exit_from_hidden_window_ends_event_loop(self):
        code='''
import tempfile
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from panel import App
app=QApplication([])
with tempfile.TemporaryDirectory() as tmp:
    profile=Path(tmp);(profile/'Settings').mkdir()
    window=App(profile,demo=True,network=False);window.show();expired=[]
    def close():window.hide();window.exit_app()
    def timeout():expired.append(True);app.quit()
    QTimer.singleShot(300,close);QTimer.singleShot(4000,timeout)
    app.exec()
    assert not expired, 'Hidden archive did not quit'
    assert not window.worker.is_alive(), 'Index writer must stop before quit'
    assert not window.shared.is_alive(), 'Shared service must stop before quit'
'''
        result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).parent,env=dict(os.environ,QT_QPA_PLATFORM='offscreen'),capture_output=True,text=True,timeout=12)
        self.assertEqual(result.returncode,0,result.stderr)
    def test_fresh_local_exit_request_closes_archive(self):
        code='''
import json,tempfile,time
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from panel import App
app=QApplication([])
with tempfile.TemporaryDirectory() as tmp:
    profile=Path(tmp);(profile/'Settings').mkdir();window=App(profile,demo=True,network=False);window.show();expired=[]
    def request():(window.data/'panel-exit-request.json').write_text(json.dumps({'action':'exit','requested_at':time.time()}))
    QTimer.singleShot(200,request);QTimer.singleShot(4000,lambda:(expired.append(True),app.quit()))
    app.exec();assert not expired
'''
        result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).parent,env=dict(os.environ,QT_QPA_PLATFORM='offscreen'),capture_output=True,text=True,timeout=12)
        self.assertEqual(result.returncode,0,result.stderr)
