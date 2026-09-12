"""UI-owned query state: background changes never invalidate an in-flight answer."""
from collections import OrderedDict


class QueryState:
    def __init__(self, cache_size=24):
        self.key = None
        self.token = 0
        self.pending = False
        self.dirty = False
        self.cache = OrderedDict()
        self.cache_size = cache_size

    def select(self, key):
        if key == self.key:
            return False
        self.invalidate()
        self.key = key
        return True

    def invalidate(self):
        self.token += 1
        self.key = None
        self.pending = self.dirty = False

    def request(self):
        if self.pending:
            self.dirty = True
            return None
        self.token += 1
        self.pending = True
        self.dirty = False
        return self.token

    def finish(self, token, result=None):
        if token != self.token or not self.pending:
            return False
        self.pending = False
        if result is not None and self.cache_size:
            self.cache[self.key] = result
            self.cache.move_to_end(self.key)
            while len(self.cache) > self.cache_size:
                self.cache.popitem(last=False)
        return True

    def cached(self):
        result = self.cache.get(self.key)
        if result is not None:
            self.cache.move_to_end(self.key)
        return result
