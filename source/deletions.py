"""Individual CLEARMSG events, distinct from CLEARCHAT bans and timeouts."""
from collections import OrderedDict
import datetime as dt
import json
import os
from pathlib import Path
import time
from storage import name


class DeletionCapture:
    def __init__(self, folder, limit=100000, ttl=1800, clock=time.monotonic):
        self.path = Path(folder) / 'deletions.jsonl'
        self.buffer = OrderedDict();self.limit = limit;self.ttl = ttl;self.clock = clock
        self.seen = OrderedDict();self.ready = False

    def prune(self):
        now = self.clock()
        while self.buffer:
            key, (at, _) = next(iter(self.buffer.items()))
            if len(self.buffer) <= self.limit and now-at <= self.ttl:break
            self.buffer.popitem(last=False)

    def remember(self, record):
        key = (record['channel'], record['id'])
        self.buffer.pop(key, None);self.buffer[key] = (self.clock(), dict(record));self.prune()

    def retain_channels(self, channels):
        allowed = set(channels)
        for key in list(self.buffer):
            if key[0] not in allowed:self.buffer.pop(key)

    def event(self, params, tags, now_ms=None):
        if not params or not tags.get('target-msg-id'):return None
        channel = name(params[0]);message_id = tags['target-msg-id']
        self.prune();cached = self.buffer.get((channel, message_id))
        original = dict(cached[1]) if cached else None
        login = tags.get('login') or (original or {}).get('user', '')
        user = name(login) if login else ''
        stamp = int(tags.get('tmi-sent-ts') or now_ms or time.time()*1000)
        if original and original['user'] != user:original = None
        if original is None:
            original = {'id': message_id, 'user': user, 'display_name': user, 'channel': channel,
                        'time_utc': '', 'text': params[1] if len(params)>1 else '',
                        'state': 'available' if len(params)>1 else 'unavailable'}
        else:original['state'] = 'available'
        return {'kind':'delete', 'user':user, 'channel':channel, 'at_ms':stamp,
                'message_id':message_id, 'message':original, 'context':[], 'source':'twitch-clearmsg',
                'time_basis':'server' if tags.get('tmi-sent-ts') else 'observed', 'raw':''}

    def append(self, event):
        key = (event['channel'], event['message_id'])
        if key in self.seen:return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.ready:
            # Preserve an interrupted trailing write and resume only this writer's file.
            if self.path.exists() and self.path.stat().st_size:
                with self.path.open('r+b') as f:
                    size=f.seek(0,2);start=max(0,size-2*1024*1024);f.seek(start);tail=f.read()
                    if not tail.endswith(b'\n'):
                        end=tail.rfind(b'\n')+1
                        if start and not end:raise OSError('Слишком длинная неполная запись удаления.')
                        self.path.with_suffix('.partial-'+str(time.time_ns())).write_bytes(tail[end:])
                        f.seek(start+end);f.truncate();f.flush();os.fsync(f.fileno())
            self.ready=True
        with self.path.open('ab') as f:
            f.write((json.dumps(event,ensure_ascii=False,separators=(',',':'))+'\n').encode())
            f.flush();os.fsync(f.fileno())
        self.seen[key]=None
        if len(self.seen)>20000:self.seen.popitem(last=False)
        return True
