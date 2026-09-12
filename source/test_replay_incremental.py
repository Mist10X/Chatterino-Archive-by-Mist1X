import copy,tempfile,unittest
from pathlib import Path
from PySide6.QtGui import QImage,QColor,QTextCursor
from replay_view import ChatBrowser
from replay_media import make_view_media
from test_qt import qt

class IncrementalReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name);(self.path/'assets').mkdir()
        self.media=make_view_media(self.path,self.path,{},None);self.chat=ChatBrowser(self.media);self.chat.resize(650,400);self.chat.show();qt.processEvents()
    def tearDown(self):self.chat.close();self.media.close();self.chat.deleteLater();qt.processEvents();self.tmp.cleanup()
    def rows(self,start=0,count=250):
        return [dict(seq=i+1,kind='message',at=i*1000,user='someone',text='Строка '+str(i),
            reply={'state':'available','user':'other','text':'Ответ на текст'} if i%7==0 else None) for i in range(start,start+count)]
    def test_unchanged_rows_do_not_rebuild_document(self):
        rows=self.rows();self.chat.show_rows(rows,set(),0,True);qt.processEvents();revision=self.chat.document().revision()
        for _ in range(20):self.chat.show_rows(copy.deepcopy(rows),set(),0,True)
        self.assertEqual(self.chat.document().revision(),revision)
        self.assertEqual(self.chat.document().blockCount(),250)
    def test_rolling_updates_match_fresh_render_with_replies_and_moderation(self):
        self.chat.show_rows(self.rows(),set(),0,True)
        for start in (1,2,20,21,70):
            rows=self.rows(start);dim={r['seq'] for r in rows if r['seq']%5==0}
            rows[-1]['kind']='timeout';rows[-1]['duration']=60
            self.chat.show_rows(rows,dim,0,True)
            reference=ChatBrowser(self.media);reference.show_rows(rows,dim,0,True)
            self.assertEqual(self.chat.toPlainText(),reference.toPlainText())
            self.assertEqual(self.chat.document().blockCount(),250)
            for i in (0,10,100,249):
                a=QTextCursor(self.chat.document().findBlockByNumber(i));b=QTextCursor(reference.document().findBlockByNumber(i))
                a.movePosition(QTextCursor.EndOfBlock);b.movePosition(QTextCursor.EndOfBlock)
                self.assertEqual(a.charFormat().foreground().color(),b.charFormat().foreground().color())
            reference.close();reference.deleteLater();qt.processEvents()
    def test_seek_back_clear_and_clock_switch(self):
        self.chat.show_rows(self.rows(300),set(),0,True);self.chat.show_rows(self.rows(3,10),{5},0,False)
        self.assertEqual(self.chat.document().blockCount(),10);self.assertIn('Строка 3',self.chat.toPlainText());self.assertNotIn('Строка 300',self.chat.toPlainText())
        self.chat.show_rows([],set(),0,False);self.assertEqual(self.chat.toPlainText(),'')
    def test_measurement_does_not_activate_animated_images(self):
        ref={'key':'c'*64,'file':'c'*64+'.png','animated':True}
        pic=QImage(72,72,QImage.Format_ARGB32);pic.fill(QColor('#123456'));pic.save(str(self.path/'assets'/ref['file']))
        self.assertFalse(self.media.measure_image(ref).isNull());self.assertNotIn(ref['key'],self.media.images)
        self.assertFalse(self.media.image(ref).isNull());self.assertIn(ref['key'],self.media.images)
    def test_reading_old_rows_does_not_force_scroll_to_bottom(self):
        self.chat.show_rows(self.rows(),set(),0,True);qt.processEvents();bar=self.chat.verticalScrollBar();bar.setValue(bar.maximum()//2)
        before=bar.value();self.chat.show_rows(self.rows(1),set(),0,True);qt.processEvents()
        self.assertLess(bar.value(),bar.maximum());self.assertLess(abs(bar.value()-before),100)
