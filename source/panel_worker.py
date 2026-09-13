import threading, queue, time, sqlite3
from storage import ArchiveIndex
from moderation_store import ModerationIndex

class PendingTasks(queue.Queue):
    """Keep the latest waiting query per view; exports are never replaced."""
    def _put(self, item):
        if item[0] in ('query', 'moderation', 'user_card'):
            for i, previous in enumerate(self.queue):
                if previous[0] == item[0]:
                    del self.queue[i]
                    self.unfinished_tasks -= 1
                    break
        super()._put(item)

class Worker(threading.Thread):
    def __init__(self, data):
        super().__init__(daemon=True)
        self.data = data
        self.tasks = PendingTasks()
        self.results = queue.Queue()
        self.stopped = threading.Event()

    def run(self):
        index = None
        moderation = None
        try:
            from pathlib import Path
            cached=(Path(self.data)/'panel-cache/archive.sqlite3').is_file()
            index = ArchiveIndex(self.data)
            moderation = ModerationIndex(self.data)
            from user_history import UserHistory
            users=UserHistory(index,moderation)
            # Serve the persisted index before importing any new source records.
            next_sync = time.monotonic() + .4 if cached else 0
            while not self.stopped.is_set() or not self.tasks.empty():
                if not self.stopped.is_set() and time.monotonic() >= next_sync:
                    for store, event in ((index, 'changed'), (moderation, 'moderation_changed')):
                        try:
                            if store.sync(max_seconds=.1):
                                self.results.put((event, None))
                        except (OSError, ValueError, sqlite3.OperationalError) as exc:
                            self.results.put(('sync_error', str(exc)))
                    next_sync = time.monotonic() + .4
                try:
                    task, payload = self.tasks.get(timeout=max(.01, next_sync-time.monotonic()))
                except queue.Empty:
                    continue
                try:
                    if task == 'query' and not self.stopped.is_set():
                        token, filters, offset = payload
                        count, rows = index.query(*filters, offset=offset)
                        self.results.put(('query', (token, count, rows, index.counts(), index.invalid_count())))
                    elif task == 'user_card' and not self.stopped.is_set():
                        token,filters,offset=payload
                        result=users.query(*filters,offset=offset)
                        self.results.put(('user_card',(token,result)))
                    elif task == 'export':
                        destination, filters = payload
                        total = index.export(destination, *filters)
                        self.results.put(('export', (destination, total)))
                    elif task == 'moderation' and not self.stopped.is_set():
                        token, filters, offset, search, extra = payload[:5]
                        people_limit = payload[5] if len(payload)>5 else 200
                        result = moderation.query(*filters, offset=offset, exact_user=True)
                        result['people'] = moderation.users(search, extra,limit=people_limit)
                        result['global_counts'] = moderation.totals()
                        result['personal_counts'] = moderation.totals(filters[0])
                        self.results.put(('moderation', (token, result)))
                except Exception as exc:
                    self.results.put((task+'_error', str(exc) if task == 'export' else (payload[0], str(exc))))
                finally:
                    self.tasks.task_done()
        except Exception as exc:
            self.results.put(("error", str(exc)))
        finally:
            if index:
                index.close()
            if moderation:
                moderation.close()


