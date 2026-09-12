"""Reproduce live-update races with a real Qt view and controlled query completion."""
import queue
import threading
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_qt import qt
from panel import App
from storage import Control


class ManualWorker:
    def __init__(self, data):
        self.tasks = queue.Queue()
        self.results = queue.Queue()
        self.stopped = threading.Event()

    def start(self): pass
    def is_alive(self): return False


def message(user, ident='1'):
    return dict(user=user, channel='one', id=ident, time_utc='2026-09-09T12:00:00Z',
                display_name=user, text='Сохранённое сообщение', reply=None)


class LoadingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.profile = Path(self.tmp.name)
        data = self.profile / 'Plugins/history-tools/data'
        data.mkdir(parents=True)
        control = Control(data)
        control.users = {'alpha': True, 'beta': True}
        control.channels = ['one']
        control.save()
        with patch('panel.Worker', ManualWorker):
            self.app = App(self.profile, demo=True)
        self.app.timer.stop()

    def tearDown(self):
        self.app.close()
        self.app.deleteLater()
        qt.processEvents()
        self.tmp.cleanup()

    def latest_request(self, kind='query'):
        found = None
        while not self.app.worker.tasks.empty():
            task, payload = self.app.worker.tasks.get_nowait()
            if task == kind: found = payload
        self.assertIsNotNone(found)
        return found

    def deliver(self, request, total=42):
        token, filters, offset = request
        self.app.worker.results.put(('query', (token, total, [message(filters[0])],
                                              {'alpha': 42, 'beta': 840}, 0)))
        self.app.poll()

    def test_live_update_does_not_discard_pending_user_result(self):
        self.deliver(self.latest_request())
        self.app.choose_user('beta')
        request = self.latest_request()
        for _ in range(5):
            self.app.worker.results.put(('changed', None))
            self.app.poll()
        self.deliver(request, 840)
        self.assertEqual(self.app.total, 840)
        self.assertEqual(self.app.rows[0]['user'], 'beta')
        self.assertEqual(self.app.messages.topLevelItemCount(), 1)
        self.assertIn('840', self.app.counter.text())

    def test_switch_clears_previous_count_and_does_not_show_old_user(self):
        self.deliver(self.latest_request())
        self.app.request_query()
        stale = self.latest_request()
        self.app.choose_user('beta')
        current = self.latest_request()
        self.assertEqual(self.app.total, 0)
        self.assertEqual(self.app.rows, [])
        self.assertIn('Загружа', self.app.counter.text())
        self.assertFalse(self.app.next.isEnabled())
        self.deliver(stale)
        self.assertEqual(self.app.messages.topLevelItemCount(), 0)
        self.deliver(current, 840)
        self.assertEqual(self.app.rows[0]['user'], 'beta')

    def test_cached_page_is_instant_and_isolated_by_filter(self):
        self.deliver(self.latest_request())
        self.app.choose_user('beta');self.deliver(self.latest_request(), 840)
        self.app.choose_user('alpha')
        self.assertEqual(self.app.rows[0]['user'], 'alpha')
        self.assertEqual(self.app.total, 42)
        self.assertIn('Обновляем', self.app.loading.text())
        stale=self.latest_request()
        self.app.search.setText('другой текст')
        self.assertEqual(self.app.rows, [])
        self.deliver(stale)
        self.assertEqual(self.app.rows, [])
        self.app.reload()
        self.assertEqual(self.latest_request()[1][2], 'другой текст')

    def test_query_error_is_visible_and_can_be_retried(self):
        request=self.latest_request()
        self.app.worker.results.put(('query_error',(request[0],'database is locked')))
        self.app.poll()
        self.assertIn('Не удалось загрузить',self.app.counter.text())
        self.assertIn('F5',self.app.loading.text())
        self.app.reload()
        self.deliver(self.latest_request())
        self.assertEqual(self.app.total,42)
        self.assertEqual(self.app.loading.text(),'')

    def test_moderation_changes_do_not_starve_the_user_card(self):
        from moderation_store import ModerationIndex
        self.latest_request()
        view=self.app.mod_view
        view.open_user('beta')
        request=self.latest_request('moderation')
        for _ in range(5):
            self.app.worker.results.put(('moderation_changed',None));self.app.poll()
        store=ModerationIndex(self.app.data)
        store.ingest(dict(kind='ban',user='beta',channel='one',at_ms=1788955200000))
        store.db.commit()
        result=store.query('beta',kind='ban',exact_user=True)
        result.update(people=store.users(),global_counts=store.totals(),personal_counts=store.totals('beta'))
        store.close()
        self.app.worker.results.put(('moderation',(request[0],result)));self.app.poll()
        self.assertEqual(view.title.text(),'@beta')
        self.assertEqual(view.ban_count.text(),'1')
        self.assertEqual(view.tree.topLevelItemCount(),1)


if __name__ == '__main__': unittest.main()
