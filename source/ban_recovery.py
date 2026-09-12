"""Durable, paced retry queue. Owned exclusively by the shared-service thread."""
import sqlite3,time
from contextlib import closing
from storage import name

class Recovery:
    def __init__(self,service):
        self.service=service;self.next_scan=0;self.next_fetch=0;self.was_connected=False
        service.store.folder.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(service.store.folder/'recovery.sqlite3');self.db.row_factory=sqlite3.Row
        self.db.execute('''CREATE TABLE IF NOT EXISTS pending(key TEXT PRIMARY KEY,user TEXT,at_ms INTEGER,
            attempts INTEGER DEFAULT 0,due REAL DEFAULT 0,state TEXT DEFAULT 'pending',reason TEXT DEFAULT '')''');self.db.commit()
    def close(self):self.db.close()
    def scan(self,now):
        path=self.service.data/'panel-cache/moderation.sqlite3'
        if not path.exists():return
        try:
            with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=1)) as source:
                records=source.execute("SELECT key,user,at_ms FROM actions WHERE kind='ban' AND at_ms>? ORDER BY at_ms DESC LIMIT 500",(int((now-2*86400)*1000),)).fetchall()
                completed={r[0] for r in source.execute("SELECT m.local_key FROM shared_matches m JOIN shared_events s ON s.key=m.shared_key WHERE s.context<>'[]'")}
            with self.db:
                for key,user,at in records:self.db.execute('INSERT OR IGNORE INTO pending(key,user,at_ms,due) VALUES(?,?,?,?)',(key,user,at,now+5))
                for key in completed:self.db.execute("UPDATE pending SET state='complete',reason='' WHERE key=?",(key,))
        except sqlite3.Error:return
    def prioritize(self,user):
        user=name(user)
        path=self.service.data/'panel-cache/moderation.sqlite3'
        try:
            with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=1)) as source:
                records=source.execute("SELECT key,user,at_ms FROM actions WHERE kind='ban' AND user=? ORDER BY at_ms DESC LIMIT 50",(user,)).fetchall()
            with self.db:
                for key,login,at in records:self.db.execute('INSERT OR IGNORE INTO pending(key,user,at_ms) VALUES(?,?,?)',(key,login,at))
        except sqlite3.Error:pass
        with self.db:self.db.execute("UPDATE pending SET due=0,attempts=0,state='pending' WHERE user=? AND state<>'complete'",(user,))
    def step(self,now=None):
        from shared_bans import AuthRequired,SharedError
        now=time.time() if now is None else now
        state=self.service.connection_state();connected=state.get('phase')=='connected'
        if connected and not self.was_connected:
            with self.db:self.db.execute("UPDATE pending SET due=? WHERE state='pending' AND reason='connection'",(now,))
            self.next_fetch=min(self.next_fetch,now)
        self.was_connected=connected
        if now>=self.next_scan:self.next_scan=now+5;self.scan(now)
        if not self.service.network or not self.service.account or self.service.auth or not self.service.commands.empty():return
        if now<self.next_fetch or time.monotonic()-self.service.last_request<15:return
        row=self.db.execute("SELECT * FROM pending WHERE state='pending' AND due<=? ORDER BY due,at_ms DESC LIMIT 1",(now,)).fetchone()
        if not row:return
        self.next_fetch=now+15
        try:
            # Known IDs can be queried even while the separate Twitch connection is down.
            self.service.fetch(row['user'],True)
            attempt=row['attempts']+1;delays=[30,120,600,3600,21600]
            with self.db:self.db.execute("UPDATE pending SET attempts=?,due=?,state=?,reason='waiting_for_context' WHERE user=? AND state='pending'",
                (attempt,now+delays[min(attempt-1,4)],'pending' if attempt<5 else 'unavailable',row['user']))
            self.next_scan=0
            self.service.result('recovery_status','Проверен контекст бана @'+row['user']+'. Полученные данные добавляются в историю.')
        except (AuthRequired,SharedError):
            # Network/auth failure does not spend the finite content-retry budget.
            with self.db:self.db.execute("UPDATE pending SET due=?,reason='connection' WHERE user=? AND state='pending'",(now+120,row['user']))
            self.service.result('recovery_status','Контекст банов ожидает подключения или ответа сервиса. Запросы сохранены.')
        except (OSError,ValueError,sqlite3.Error):
            with self.db:self.db.execute("UPDATE pending SET due=?,reason='connection' WHERE key=?",(now+120,row['key']))
