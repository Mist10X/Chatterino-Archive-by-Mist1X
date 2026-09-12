import test_qt as fixture
from test_qt import qt,wait_until
from PySide6.QtCore import Qt,QPoint
from stable_tree import KEY_ROLE
import unittest,time

class ResponsiveTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixture.QtTests();self.fixture.setUp();self.app=self.fixture.app
    def tearDown(self):self.fixture.tearDown()
    def settle(self):
        for _ in range(10):qt.processEvents();time.sleep(.01)
    def test_all_pages_fit_half_screen_and_small_windows(self):
        a=self.app
        for width,height in ((960,1000),(768,820),(640,600),(1600,1000)):
            a.resize(width,height)
            for index in range(3):
                a.select_page(index);self.settle();page=a.pages.currentWidget()
                self.assertEqual(a.width(),width)
                self.assertEqual(page.horizontalScrollBar().maximum(),0,(width,index))
                for tree in ([a.messages] if index==0 else [a.mod_view.tree] if index==1 else [a.replay_view.list]):
                    self.assertEqual(tree.horizontalScrollBar().maximum(),0,(width,index))
            self.assertEqual(a.responsive.compact,width<1180)
            if index==1:self.assertTrue(a.mod_view.total_caption.text() in ('Всего в архиве:','У пользователя:'))
    def test_user_picker_restores_sidebar_and_keeps_selection(self):
        a=self.app;a.resize(960,1000);a.select_page(1);self.settle()
        view=a.mod_view;wait_until(lambda:view.people.topLevelItemCount()>0)
        side=a.responsive.sidebars[1][0];self.assertFalse(side.isVisible())
        a.responsive.focus_search();self.settle();self.assertIsNotNone(a.responsive.dialog)
        self.assertTrue(view.search.isVisible())
        item=view.people.topLevelItem(0);view.people.setCurrentItem(item);view.people.itemClicked.emit(item,0)
        wait_until(lambda:a.responsive.dialog is None)
        self.assertEqual(view.user,item.data(0,Qt.UserRole));self.assertFalse(side.isVisible())
        a.resize(1600,1000);self.settle();self.assertTrue(side.isVisible())
        self.assertIs(side.parentWidget(),view.frame.widget())
    def test_compact_rows_and_splitters_survive_background_refresh(self):
        a=self.app;a.resize(960,1000);a.select_page(1);self.settle();m=a.mod_view
        wait_until(lambda:m.total==4)
        self.assertIn('\n',m.tree.topLevelItem(0).text(0))
        self.assertIn('\n#one',m.tree.topLevelItem(0).text(1))
        self.assertIn('Удалённый текст',m.tree.topLevelItem(0).text(3))
        splitter=a.responsive.splitters[1];splitter.setSizes([240,300]);sizes=splitter.sizes()
        m.tree.setCurrentItem(m.tree.topLevelItem(0));key=m.tree.currentItem().data(0,KEY_ROLE)
        m.request();wait_until(lambda:not m.query.pending);self.settle()
        self.assertEqual(splitter.sizes(),sizes);self.assertEqual(m.tree.currentItem().data(0,KEY_ROLE),key)
        m.open_user('someone');wait_until(lambda:m.total==3)
        self.assertFalse(m.tree.isColumnHidden(1));self.assertEqual(m.tree.headerItem().text(1),'Канал')
        self.assertTrue(m.tree.topLevelItem(0).text(1).startswith('#'))
        a.resize(1600,1000);self.settle();self.assertFalse(m.tree.isColumnHidden(4))
        self.assertNotIn('\n',m.tree.topLevelItem(0).text(0))
