import unittest
import test_qt as fixture
from test_qt import qt,wait_until
from test_user_history import card
from shared_bans import Store
from PySide6.QtCore import Qt

class UserViewTests(unittest.TestCase):
    def setUp(self):self.fixture=fixture.QtTests();self.fixture.setUp();self.app=self.fixture.app
    def tearDown(self):self.fixture.tearDown()
    def test_unknown_nick_shared_ban_moderation_counts_and_html(self):
        Store(self.app.data).save(card(user='untracked'));self.app.open_user_card('untracked');view=self.app.user_view
        wait_until(lambda:view.total>=1);view.select('bans');wait_until(lambda:view.total==1 and not view.query.pending)
        self.assertNotIn('untracked',self.app.control.users)
        self.assertIn('AutoMod',view.preview.toPlainText());self.assertIn('<b>не HTML</b>',view.preview.toPlainText())
        self.app.mod_view.open_user('untracked');wait_until(lambda:self.app.mod_view.total==1)
        self.assertEqual(self.app.mod_view.ban_count.text(),'1')
        self.app.mod_view.tree.setCurrentItem(self.app.mod_view.tree.topLevelItem(0))
        self.assertIn('AutoMod',self.app.mod_view.preview.toPlainText())
    def test_tabs_filters_nickname_change_do_not_show_old_rows(self):
        app=self.app;app.open_user_card('someone');v=app.user_view
        wait_until(lambda:v.total>100);v.select('messages');wait_until(lambda:v.total==126)
        v.page(1);wait_until(lambda:len(v.rows)==26)
        v.open_user('nobody_here');self.assertEqual(v.tree.topLevelItemCount(),0)
        wait_until(lambda:not v.query.pending);self.assertEqual(v.total,0)
        v.open_user('someone');v.select('bans');wait_until(lambda:v.total==1 and not v.query.pending)
        self.assertTrue(all(r['kind']=='ban' and r['user']=='someone' for r in v.rows))
    def test_new_page_fits_narrow_sizes(self):
        self.app.open_user_card('someone');v=self.app.user_view;wait_until(lambda:v.total>0)
        for width,height in ((640,600),(960,950),(1600,1000)):
            self.app.resize(width,height)
            for _ in range(5):qt.processEvents()
            self.assertEqual(v.frame.horizontalScrollBar().maximum(),0)
            self.assertEqual(v.tree.horizontalScrollBar().maximum(),0)
            self.assertEqual(self.app.width(),width)
