import unittest
import test_qt as fixture
from test_qt import qt,wait_until
from shared_bans import parse_card
from shared_bans_view import open_shared
from test_shared_bans import RAW

class SharedQtTests(unittest.TestCase):
    def setUp(self):self.fixture=fixture.QtTests();self.fixture.setUp();self.app=self.fixture.app
    def tearDown(self):
        self.app.close();wait_until(lambda:not self.app.shared.is_alive());self.fixture.tearDown()
    def test_cached_automod_plain_text_channels_and_local_counts(self):
        app=self.app;app.select_page(1);wait_until(lambda:app.mod_view.total==4)
        old=app.mod_view.ban_count.text();app.shared.store.save(parse_card(RAW,'123','someone'))
        open_shared(app,'someone');dialog=app.shared_window
        wait_until(lambda:dialog.tree.topLevelItemCount()==1)
        self.assertIn('AutoMod',dialog.preview.toPlainText());self.assertIn('<b>текст</b>',dialog.preview.toPlainText())
        self.assertEqual(dialog.tree.topLevelItem(0).text(0),'#one')
        wait_until(lambda:app.mod_view.ban_count.text()==str(int(old)+1))
        dialog.resize(640,600);qt.processEvents();self.assertEqual(dialog.width(),640)
        self.assertTrue(dialog.refresh.isVisible());self.assertTrue(dialog.preview.isVisible())
        self.assertLessEqual(dialog.refresh.geometry().right(),dialog.refresh.parentWidget().width())
        dialog.close();wait_until(lambda:app.shared_window is None)
        open_shared(app,'someone');wait_until(lambda:app.shared_window.tree.topLevelItemCount()==1)
