import tempfile
from pathlib import Path
import unittest
from PySide6.QtCore import QEvent,QPoint,Qt
from PySide6.QtGui import QImage,QColor,QTextCursor,QHelpEvent
from PySide6.QtWidgets import QToolTip
from replay_view import ChatBrowser
from replay_media import make_view_media
from emote_widgets import GROUP,measured
from test_qt import qt,wait_until

class BadgeTests(unittest.TestCase):
    def test_old_multword_badge_is_image_without_text_tooltip_or_copy_name(self):
        with tempfile.TemporaryDirectory() as temp:
            data=Path(temp);folder=data/'recording';(folder/'assets').mkdir(parents=True)
            ref={'name':'9-Month Subscriber RuneScape Skull','key':'a'*64,'file':'a'*64+'.png','animated':False}
            pic=QImage(72,72,QImage.Format_ARGB32);pic.fill(QColor('#f0b050'));pic.save(str(folder/'assets'/ref['file']))
            media=make_view_media(data,folder,{},None);chat=ChatBrowser(media);chat.resize(800,150);chat.show()
            try:
                chat.show_rows([{'seq':1,'kind':'message','at':1000,'user':'xxiner_','text':'так ет хрень','badge_refs':[ref]}],set(),0,True);qt.processEvents()
                self.assertEqual(chat.toPlainText(),'00:00:01  xxiner_: так ет хрень')
                cursor=QTextCursor(chat.document());cursor.setPosition(10);cursor.setPosition(11,QTextCursor.KeepAnchor)
                self.assertEqual(cursor.selectedText(),'\ufffc');group=cursor.charFormat().property(GROUP)
                self.assertTrue(group['badge']);self.assertEqual(cursor.charFormat().toolTip(),'')
                width,_,pictures=measured(group,media,chat.font(),18);self.assertEqual(width,22);self.assertTrue(pictures)
                cursor.setPosition(10);rect=chat.cursorRect(cursor);point=QPoint(rect.x()+8,rect.center().y())
                self.assertIsNone(chat.emote_at(point))
                QToolTip.showText(chat.mapToGlobal(point),'previous',chat)
                qt.sendEvent(chat.viewport(),QHelpEvent(QEvent.ToolTip,point,chat.viewport().mapToGlobal(point)))
                self.assertNotIn(ref['name'],QToolTip.text())
                # Actual raster output contains the badge color; this is not a text placeholder.
                rendered=chat.grab().toImage()
                self.assertTrue(any(rendered.pixelColor(x,y).name()=='#f0b050' for x in range(rendered.width()) for y in range(rendered.height())))
            finally:chat.close();media.close();chat.deleteLater();qt.processEvents()
    def test_unavailable_and_corrupt_badges_take_no_space_then_arrive(self):
        with tempfile.TemporaryDirectory() as temp:
            data=Path(temp);folder=data/'recording';(folder/'assets').mkdir(parents=True)
            ref={'name':'Long badge name','key':'b'*64,'file':'b'*64+'.png','animated':False}
            media=make_view_media(data,folder,{},None);chat=ChatBrowser(media);chat.resize(800,150);chat.show()
            try:
                row={'seq':1,'kind':'message','at':1000,'user':'someone','text':'Привет','badge_refs':[ref]}
                chat.show_rows([row],set(),0,True);qt.processEvents()
                cursor=QTextCursor(chat.document());cursor.setPosition(10);before=chat.cursorRect(cursor).x();cursor.setPosition(11);after=chat.cursorRect(cursor).x()
                self.assertEqual(before,after);self.assertEqual(chat.toPlainText(),'00:00:01  someone: Привет')
                group={'text':'','badge':True,'emotes':[ref]};self.assertEqual(measured(group,media,chat.font(),18)[0],0)
                (folder/'assets'/ref['file']).write_bytes(b'not an image');self.assertEqual(measured(group,media,chat.font(),18)[0],0)
                pic=QImage(72,72,QImage.Format_ARGB32);pic.fill(QColor('#aaffaa'));pic.save(str(folder/'assets'/ref['file']))
                media.poll();chat.refresh_emotes();qt.processEvents();self.assertEqual(measured(group,media,chat.font(),18)[0],22)
                self.assertNotIn('Long badge name',chat.toPlainText())
            finally:chat.close();media.close();chat.deleteLater();qt.processEvents()
