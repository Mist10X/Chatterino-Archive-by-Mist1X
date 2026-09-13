import unittest,tempfile,json,time
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt,QPoint,QRectF
from PySide6.QtTest import QTest
from PySide6.QtGui import QFontInfo,QImage,QPixmap,QPainter
import test_qt as fixture
from test_qt import qt,wait_until
from ui_controls import ChannelCombo
from profiles import Profiles

class InterfaceTests(unittest.TestCase):
    def setUp(self):self.fixture=fixture.QtTests();self.fixture.setUp();self.app=self.fixture.app
    def tearDown(self):self.fixture.tearDown()
    def test_channel_tab_cycles_and_enter_commits_single_match(self):
        c=self.app.mod_view.channel;c.blockSignals(True);c.clear();c.addItems(['Все каналы','dangerlyoha','dangen','kopsteep']);c.blockSignals(False)
        self.app.select_page(1);c.setFocus();c.lineEdit().selectAll();QTest.keyClicks(c.lineEdit(),'dang')
        QTest.keyClick(c.lineEdit(),Qt.Key_Tab);self.assertEqual(c.lineEdit().text(),'dangerlyoha')
        QTest.keyClick(c.lineEdit(),Qt.Key_Tab);self.assertEqual(c.lineEdit().text(),'dangen')
        QTest.keyClick(c.lineEdit(),Qt.Key_Backtab);self.assertEqual(c.lineEdit().text(),'dangerlyoha')
        QTest.keyClick(c.lineEdit(),Qt.Key_Return);self.assertEqual(c.currentText(),'dangerlyoha')
        c.lineEdit().selectAll();QTest.keyClicks(c.lineEdit(),'kop');QTest.keyClick(c.lineEdit(),Qt.Key_Return);self.assertEqual(c.currentText(),'kopsteep')
    def test_filter_all_none_and_entire_row_single_click(self):
        self.app.select_page(1);f=self.app.mod_view.kind;f.menu.popup(f.mapToGlobal(QPoint(0,f.height())));qt.processEvents()
        QTest.mouseClick(f.all,Qt.LeftButton,pos=QPoint(f.all.width()-8,f.all.height()//2));self.assertEqual(f.values(),())
        wait_until(lambda:not self.app.mod_view.query.pending);self.assertEqual(self.app.mod_view.total,0)
        check=f.checks[0];QTest.mouseClick(check,Qt.LeftButton,pos=QPoint(check.width()-8,check.height()//2));self.assertEqual(f.values(),('timeout',));self.assertTrue(f.menu.isVisible())
        QTest.mouseClick(check,Qt.LeftButton,pos=QPoint(check.width()-8,check.height()//2));self.assertEqual(f.values(),())
        QTest.mouseClick(f.all,Qt.LeftButton,pos=QPoint(f.all.width()-8,f.all.height()//2));self.assertEqual(len(f.values()),4);f.menu.hide()
    def test_all_surfaces_use_same_font_and_profile_identity(self):
        a=self.app;a.profiles.entries['someone']={'display_name':'SomeOne'};a.select_page(1)
        a.user_view.open_user('someone');a.refresh_profiles();self.assertEqual(a.user_view.title.text(),'@SomeOne');self.assertEqual(a.user_view.nick.text(),'someone')
        for widget in (a.mod_view.kind.all,a.mod_view.channel.completer().popup(),a.mod_view.preview,a.mod_view.search):
            self.assertIn('Montserrat',QFontInfo(widget.font()).family());self.assertGreaterEqual(widget.font().pointSizeF(),10)

class ProfileCacheTests(unittest.TestCase):
    def test_offline_names_and_images_are_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'profiles';root.mkdir();im=QImage(32,32,QImage.Format_RGB32);im.fill(Qt.red);im.save(str(root/'someone.png'))
            (root/'identities.json').write_text(json.dumps({'someone':{'display_name':'SomeOne','image':'someone.png'}}))
            p=Profiles(folder,lambda:None,False);self.assertEqual(p.display('SOMEONE'),'SomeOne');self.assertIsNotNone(p.pixmap('someone'));self.assertFalse(p.icon('someone').isNull());p.ensure('someone');self.assertFalse(p.pending);p.close()
    def test_tiny_avatars_stay_round_at_multiple_scales(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Profiles(folder,lambda:None,False);source=QPixmap(120,60);source.fill(Qt.red);p.pictures['u']=source
            for dpr in (1.,1.25,1.5,2.):
                pix=p.rounded_pixmap('u',20,dpr);im=pix.toImage()
                self.assertEqual(pix.width(),pix.height());self.assertEqual(pix.width(),round(20*dpr))
                self.assertEqual(im.pixelColor(0,0).alpha(),0)
                self.assertEqual(im.pixelColor(im.width()//2,im.height()//2).alpha(),255)
                self.assertTrue(any(0<im.pixelColor(x,y).alpha()<255 for x in range(im.width()) for y in range(im.height())))
            def draw(width):
                result=QPixmap(width,24);result.fill(Qt.transparent);painter=QPainter(result);p.channel(painter,QRectF(0,0,width,24),'u',qt.font());painter.end();return result.toImage().copy(0,2,20,20)
            self.assertEqual(draw(60),draw(180));p.close()
    def test_lookup_batches_and_image_host_has_no_auth(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Profiles(folder,lambda:'token',True);p.timer.stop();p.ensure('first');p.ensure('second');calls=[]
            p.get=lambda url,callback,token=None,limit=0:calls.append((url,callback,token))
            p.flush();self.assertEqual(len(calls),1);self.assertIn('login=first&login=second',calls[0][0]);self.assertEqual(calls[0][2],'token')
            calls[0][1](json.dumps({'data':[{'login':'first','display_name':'First','id':'1','profile_image_url':'https://static-cdn.jtvnw.net/a.png'}]}).encode())
            self.assertEqual(p.display('first'),'First');self.assertIsNone(calls[1][2]);self.assertFalse(p.inflight)
            p.download('third','https://example.com/pic.png');self.assertEqual(len(calls),2);p.close()
