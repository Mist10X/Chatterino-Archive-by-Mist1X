import json,tempfile,time,unittest
from pathlib import Path
from PySide6.QtCore import Qt
from test_qt import qt,wait_until
from stable_tree import reconcile,set_texts
from theme import table
from storage import Control
from panel import App


class ScrollTests(unittest.TestCase):
    def test_follow_new_events_only_at_top_of_latest_page(self):
        tree=table(['Event']);tree.resize(320,400);tree.show()
        rows=[dict(id=i,text=str(i)) for i in range(100)]
        def update(item,row):set_texts(item,[row['text']]);item.setData(0,Qt.UserRole,row['id'])
        def refresh(follow=True):
            reconcile(tree,rows,lambda r:r['id'],update,follow_top=follow);qt.processEvents()
        def top_id():return tree.itemAt(1,1).data(0,Qt.UserRole)
        try:
            refresh();tree.setCurrentItem(tree.topLevelItem(0));selected=tree.currentItem()
            bar=tree.verticalScrollBar()
            for ident in (-1,-2,-3):
                rows.insert(0,dict(id=ident,text='new'));refresh()
                self.assertEqual(bar.value(),bar.minimum());self.assertEqual(top_id(),ident)
                self.assertIs(tree.currentItem(),selected)
            bar.setValue(401);qt.processEvents()
            anchor=top_id();offset=tree.visualItemRect(tree.itemAt(1,1)).top()
            rows.insert(0,dict(id=-4,text='new'));refresh()
            self.assertEqual(top_id(),anchor)
            self.assertEqual(tree.visualItemRect(tree.itemAt(1,1)).top(),offset)
            self.assertIs(tree.currentItem(),selected)
            bar.setValue(0);rows.insert(0,dict(id=-5,text='new'));refresh()
            self.assertEqual(top_id(),-5)
            # Older pages must keep their anchor even with the scrollbar at zero.
            rows.insert(0,dict(id=-6,text='new'));refresh(follow=False)
            self.assertEqual(top_id(),-5)
            # Incoming data while dragging must respect where the reader releases.
            bar.setValue(0);bar.setSliderDown(True)
            rows.insert(0,dict(id=-7,text='new'));refresh()
            self.assertIsNotNone(tree.pending_rows)
            bar.setValue(401);anchor=top_id();bar.setSliderDown(False);qt.processEvents()
            self.assertEqual(top_id(),anchor)
            bar.setSliderDown(True);rows.insert(0,dict(id=-8,text='new'));refresh()
            bar.setValue(0);bar.setSliderDown(False);qt.processEvents()
            self.assertEqual(top_id(),-8);self.assertIs(tree.currentItem(),selected)
        finally:tree.close();tree.deleteLater();qt.processEvents()

    def test_refresh_insert_and_drag_preserve_reader_position_and_item(self):
        tree=table(['User']);tree.resize(320,400);tree.show()
        rows=[dict(id=i,text=str(i)) for i in range(500)]
        def update(item,row):set_texts(item,[row['text']]);item.setData(0,Qt.UserRole,row['id'])
        try:
            reconcile(tree,rows,lambda r:r['id'],update)
            tree.setCurrentItem(tree.topLevelItem(0));tree.verticalScrollBar().setValue(5000);qt.processEvents()
            anchor=tree.itemAt(1,1);ident=anchor.data(0,Qt.UserRole);offset=tree.visualItemRect(anchor).top()
            current=tree.currentItem()
            for _ in range(8):
                reconcile(tree,[dict(id=-1,text='new')]+rows,lambda r:r['id'],update)
                qt.processEvents()
                self.assertEqual(tree.itemAt(1,1).data(0,Qt.UserRole),ident)
                self.assertEqual(tree.visualItemRect(tree.itemAt(1,1)).top(),offset)
                self.assertIs(tree.currentItem(),current)
            bar=tree.verticalScrollBar();bar.setSliderDown(True)
            reconcile(tree,rows+[dict(id=600,text='later')],lambda r:r['id'],update)
            self.assertIsNotNone(tree.pending_rows)
            bar.setValue(2000);ident=tree.itemAt(1,1).data(0,Qt.UserRole)
            bar.setSliderDown(False);qt.processEvents()
            self.assertIsNone(tree.pending_rows)
            self.assertEqual(tree.itemAt(1,1).data(0,Qt.UserRole),ident)
        finally:tree.close();tree.deleteLater();qt.processEvents()

    def test_directory_loads_every_user_and_searches_beyond_first_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile=Path(tmp);data=profile/'Plugins/history-tools/data';data.mkdir(parents=True)
            events=[dict(kind='ban',user=f'user{i:04}',channel='one',at_ms=1788967200000+i) for i in range(625)]
            (data/'moderation.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events),encoding='utf-8')
            control=Control(data);control.save()
            app=App(profile,demo=True);app.show();app.select_page(1);view=app.mod_view
            try:
                wait_until(lambda:view.people_total==625)
                self.assertEqual(view.people.topLevelItemCount(),200)
                while view.people.topLevelItemCount()<625:
                    old=view.people.topLevelItemCount();view.people.verticalScrollBar().setValue(view.people.verticalScrollBar().maximum())
                    if not view.query.pending:view.load_more_people()
                    wait_until(lambda:view.people.topLevelItemCount()>old)
                self.assertEqual(view.people.topLevelItem(624).data(0,Qt.UserRole),'user0624')
                view.search.setText('user0624');view.reload()
                wait_until(lambda:view.people.topLevelItemCount()==1 and view.people.topLevelItem(0).data(0,Qt.UserRole)=='user0624')
                self.assertFalse(view.more_people.isVisible())
                view.open_user('user0624');wait_until(lambda:view.ban_count.text()=='1')
            finally:app.exit_app();wait_until(lambda:not app.worker.is_alive());app.deleteLater();qt.processEvents()

    def test_tracked_users_and_messages_do_not_jump_on_live_refresh(self):
        from test_loading_qt import message
        with tempfile.TemporaryDirectory() as tmp:
            profile=Path(tmp);data=profile/'Plugins/history-tools/data';data.mkdir(parents=True)
            control=Control(data);control.users={f'user{i:04}':True for i in range(100)};control.save()
            app=App(profile,demo=True);app.show();app.timer.stop()
            try:
                app.users.verticalScrollBar().setValue(1500);qt.processEvents()
                anchor=app.users.itemAt(1,1).data(0,Qt.UserRole)
                for _ in range(5):app.counts['user0000']=3;app.refresh_users();qt.processEvents()
                self.assertEqual(app.users.itemAt(1,1).data(0,Qt.UserRole),anchor)
                rows=[message('user0000',str(i)) for i in range(100)]
                app.render_rows(100,rows);app.messages.setCurrentItem(app.messages.topLevelItem(0))
                app.messages.verticalScrollBar().setValue(1500);qt.processEvents()
                top=app.messages.itemAt(1,1).data(0,Qt.UserRole)['id']
                for _ in range(5):app.render_rows(100,rows);qt.processEvents()
                self.assertEqual(app.messages.itemAt(1,1).data(0,Qt.UserRole)['id'],top)
            finally:app.exit_app();wait_until(lambda:not app.worker.is_alive());app.deleteLater();qt.processEvents()
