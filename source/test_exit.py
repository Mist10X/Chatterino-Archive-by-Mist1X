import os,subprocess,sys,unittest
from pathlib import Path

class ExitTests(unittest.TestCase):
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
