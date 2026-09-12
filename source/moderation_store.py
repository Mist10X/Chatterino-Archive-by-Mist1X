"""Rebuildable moderation index. Historical actions and current status are distinct."""
import datetime as dt
import json
import sqlite3
import time
from pathlib import Path

from storage import name, atomic_write
from moderation_groups import repeat_count,aggregate_target

KINDS = {'ban', 'timeout', 'unban', 'untimeout', 'speech', 'identity', 'delete'}


class ModerationIndex:
    def __init__(self, data):
        self.data = Path(data)
        self.profile = self.data.parent.parent.parent
        (self.data / 'panel-cache').mkdir(exist_ok=True)
        self.db = sqlite3.connect(self.data / 'panel-cache' / 'moderation.sqlite3', timeout=10)
        self.db.row_factory = sqlite3.Row
        self.shared_stamp = None
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS actions (
                key TEXT PRIMARY KEY, kind TEXT, user TEXT, channel TEXT, at_ms INTEGER,
                duration INTEGER, basis TEXT, sources TEXT, raw TEXT, context TEXT);
            CREATE INDEX IF NOT EXISTS action_pair_time ON actions(user,channel,at_ms DESC);
            CREATE INDEX IF NOT EXISTS action_time ON actions(at_ms DESC);
            CREATE TABLE IF NOT EXISTS aliases (key TEXT PRIMARY KEY, canonical TEXT);
            CREATE TABLE IF NOT EXISTS identities (user TEXT PRIMARY KEY,user_id TEXT,at_ms INTEGER);
            CREATE TABLE IF NOT EXISTS shared_hints (user_id TEXT PRIMARY KEY,n INTEGER,at_seconds INTEGER);
            CREATE TABLE IF NOT EXISTS ingestion (id INTEGER PRIMARY KEY,offset INTEGER,invalid INTEGER);
        ''')
        columns={r['name'] for r in self.db.execute('PRAGMA table_info(actions)')}
        for column in ('message_id','message'):
            if column not in columns:self.db.execute('ALTER TABLE actions ADD COLUMN '+column+' TEXT')
        if 'last_at_ms' not in columns:self.db.execute('ALTER TABLE actions ADD COLUMN last_at_ms INTEGER')
        if 'repeat_count' not in columns:self.db.execute('ALTER TABLE actions ADD COLUMN repeat_count INTEGER NOT NULL DEFAULT 1')
        if self.db.execute('PRAGMA user_version').fetchone()[0]<120:
            # The JSONL originals are authoritative. Rebuild only the disposable index
            # so old aggregate notifications receive the same grouping as new ones.
            if (self.data/'moderation.jsonl').exists() or (self.data/'twitch/deletions.jsonl').exists():
                for table in ('actions','aliases','ingestion'):self.db.execute('DELETE FROM '+table)
            self.db.execute('PRAGMA user_version=120')
        self.db.commit()
        from shared_moderation import SharedModeration
        self.combined=SharedModeration(self)
        from moderation_evidence import Evidence
        self.evidence=Evidence(self)

    def ingest_deletion(self,e):
        channel=name(e['channel']);user=name(e['user']) if e.get('user') else ''
        message_id=e.get('message_id');stamp=int(e['at_ms'])
        if not isinstance(message_id,str) or not message_id or len(message_id)>128 or not 0<stamp<=253402300799999:
            raise ValueError('Invalid deletion identity/time')
        message=e.get('message')
        if not isinstance(message,dict) or message.get('state') not in ('available','unavailable'):
            raise ValueError('Invalid deleted message')
        if (message.get('user','')!=user or message.get('channel')!=channel or message.get('id')!=message_id
            or not isinstance(message.get('text'),str)):
            raise ValueError('Deleted message attribution mismatch')
        reply=message.get('reply')
        if reply is not None and (not isinstance(reply,dict) or reply.get('channel')!=channel
            or reply.get('state') not in ('available','unavailable') or not isinstance(reply.get('text'),str)):
            raise ValueError('Invalid deleted message reply')
        key=channel+'|delete|'+message_id;source=str(e.get('source','unknown'))
        old=self.db.execute('SELECT * FROM actions WHERE key=?',(key,)).fetchone()
        if old:
            # The message ID identifies a deletion across sources and restarts.
            if old['user'] and user and old['user']!=user:raise ValueError('Conflicting deletion author')
            previous=json.loads(old['message'])
            score=lambda m:(m.get('state')=='available',bool(m.get('reply')),bool(m.get('time_utc')))
            best=dict(message if score(message)>score(previous) else previous)
            if not best.get('user') and user:best.update(user=user,display_name=user)
            self.db.execute('UPDATE actions SET user=?,message=?,sources=? WHERE key=?',
                (old['user'] or user,json.dumps(best,ensure_ascii=False),json.dumps(sorted(set(json.loads(old['sources']))|{source})),key))
            return 0
        self.db.execute('''INSERT INTO actions(key,kind,user,channel,at_ms,duration,basis,sources,raw,context,message_id,message)
            VALUES(?,?,?,?,?,NULL,?,?,?,'[]',?,?)''',
            (key,'delete',user,channel,stamp,str(e.get('time_basis','server')),json.dumps([source]),'',message_id,json.dumps(message,ensure_ascii=False)))
        return 1

    def ingest(self, e):
        if isinstance(e,dict) and e.get('kind') in ('executor','reward','bot_result'):return self.evidence.ingest(e)
        if not isinstance(e, dict) or e.get('kind') not in KINDS:
            raise ValueError('Unknown moderation event')
        if e['kind']=='delete':return self.ingest_deletion(e)
        user, channel = name(e['user']), name(e['channel'])
        kind, stamp = e['kind'], int(e['at_ms'])
        if stamp < 0 or stamp > 253402300799999:
            raise ValueError('Invalid timestamp')
        if kind == 'identity':
            uid = str(e.get('user_id', ''))
            if not uid.isascii() or not uid.isdecimal():
                raise ValueError('Invalid user ID')
            self.db.execute('''INSERT INTO identities VALUES(?,?,?) ON CONFLICT(user) DO UPDATE SET
                user_id=excluded.user_id,at_ms=excluded.at_ms WHERE excluded.at_ms>=identities.at_ms''', (user,uid,stamp))
            return 1
        if not stamp:
            raise ValueError('Missing action timestamp')
        duration = e.get('duration')
        if duration is not None:
            duration = int(duration)
            if duration < 0 or duration > 315360000:
                raise ValueError('Invalid timeout duration')
        key = f'{channel}|{user}|{kind}|{stamp}|{duration if duration is not None else ""}'
        source = str(e.get('source','chatterino'))
        context = e.get('context', [])
        if not isinstance(context, list) or len(context) > 10:
            raise ValueError('Invalid context')
        # Only messages from the punished user/channel preceding this action belong in its context.
        clean = []
        for m in context:
            if not isinstance(m,dict) or name(m.get('user',''))!=user or name(m.get('channel',''))!=channel:
                raise ValueError('Context attribution mismatch')
            if not isinstance(m.get('text'),str):
                raise ValueError('Invalid context text')
            received = dt.datetime.fromisoformat(m['time_utc'].replace('Z','+00:00'))
            if received.timestamp()*1000 > stamp + .5:
                raise ValueError('Context is newer than punishment')
            clean.append(m)
        if kind=='timeout':
            clean=clean[-5:]
        encoded_context=json.dumps(clean,ensure_ascii=False)
        alias=self.db.execute('SELECT canonical FROM aliases WHERE key=?',(key,)).fetchone()
        existing=self.db.execute('SELECT * FROM actions WHERE key=?',(alias[0] if alias else key,)).fetchone()
        grouped=False
        if not existing:
            existing=aggregate_target(self,e,key,user,channel,kind,stamp)
            grouped=existing is not None
        if not existing and kind in ('ban','timeout','unban','untimeout'):
            # Merge a near-simultaneous IRC/EventSub report only across different sources,
            # and never across an intervening action (e.g. unban -> a new ban).
            candidates=self.db.execute('''SELECT * FROM actions WHERE user=? AND channel=? AND kind=?
                AND duration IS ? AND abs(at_ms-?)<=1500 ORDER BY abs(at_ms-?)''',
                (user,channel,kind,duration,stamp,stamp)).fetchall()
            for candidate in candidates:
                if source in json.loads(candidate['sources']):
                    continue
                low,high=sorted((stamp,candidate['at_ms']))
                intervening=self.db.execute('''SELECT 1 FROM actions WHERE user=? AND channel=?
                    AND kind IN ('ban','timeout','unban','untimeout','speech')
                    AND at_ms>? AND at_ms<=? AND key<>? LIMIT 1''',
                    (user,channel,low,high,candidate['key'])).fetchone()
                if not intervening:
                    existing=candidate
                    break
        if existing:
            self.db.execute('INSERT OR IGNORE INTO aliases VALUES(?,?)',(key,existing['key']))
            sources=sorted(set(json.loads(existing['sources']))|{source})
            clean=[m for m in clean if dt.datetime.fromisoformat(m['time_utc'].replace('Z','+00:00')).timestamp()*1000<=existing['at_ms']+.5]
            best=json.dumps(clean,ensure_ascii=False) if len(clean)>len(json.loads(existing['context'])) else existing['context']
            last=existing['last_at_ms'] or existing['at_ms']
            update=(grouped or repeat_count(e.get('raw'))>existing['repeat_count']) and stamp>=last
            raw=str(e.get('raw','')) if update else existing['raw']
            new_duration=duration if update else existing['duration']
            count=max(existing['repeat_count'],repeat_count(raw))
            self.db.execute('UPDATE actions SET sources=?,context=?,raw=?,duration=?,last_at_ms=?,repeat_count=? WHERE key=?',
                            (json.dumps(sources),best,raw,new_duration,max(last,stamp) if update else last,count,existing['key']))
            return int(update or best!=existing['context'])
        self.db.execute('INSERT INTO actions(key,kind,user,channel,at_ms,duration,basis,sources,raw,context,last_at_ms,repeat_count,message_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (key,kind,user,channel,stamp,duration,str(e.get('time_basis','server')),json.dumps([source]),str(e.get('raw','')),encoded_context,stamp,repeat_count(e.get('raw')),e.get('message_id','')))
        return 1

    def sync(self,max_seconds=None):
        changed=0
        deadline=time.monotonic()+max_seconds if max_seconds is not None else float('inf')
        sources=((1,self.data/'moderation.jsonl'),(2,self.data/'twitch/deletions.jsonl'),(3,self.data/'twitch/identities.jsonl'),(4,self.data/'twitch/activity.jsonl'))
        start=getattr(self,'_sync_cursor',0)%len(sources)
        for step in range(len(sources)):
            pos=(start+step)%len(sources);ingestion_id,path=sources[pos];self._sync_cursor=(pos+1)%len(sources)
            if not path.exists():continue
            progress=self.db.execute('SELECT offset,invalid FROM ingestion WHERE id=?',(ingestion_id,)).fetchone()
            offset,invalid=(progress[0],progress[1]) if progress else (0,0)
            size=path.stat().st_size
            if progress is not None and size==offset:continue
            if size<offset:
                offset,invalid=0,0
            with path.open('rb') as f,self.db:
                f.seek(offset)
                for i in range(10000):
                    if i and i%32==0 and time.monotonic()>=deadline:break
                    line=f.readline(2*1024*1024)
                    if not line or not line.endswith(b'\n'):
                        break
                    offset=f.tell()
                    if not line.strip():
                        continue
                    try:
                        changed+=self.ingest(json.loads(line))
                    except (ValueError,TypeError,KeyError,OverflowError):
                        invalid+=1
                self.db.execute('INSERT OR REPLACE INTO ingestion VALUES(?,?,?)',(ingestion_id,offset,invalid))
            if time.monotonic()>=deadline:break
        self.sync_shared()
        return changed+int(self.combined.sync(bool(changed)))

    def sync_shared(self):
        # The fork stores only a count and last observation time, without channel identities.
        live=self.profile/'Misc'/'sharedbans-seen.json'
        saved=self.data/'shared-bans-summary.json'
        path=live if live.exists() else saved
        if not path.exists():
            return
        stamp=(str(path),path.stat().st_mtime_ns,path.stat().st_size)
        if stamp==self.shared_stamp:
            return
        if path.stat().st_size>10*1024*1024:
            return
        try:
            obj=json.loads(path.read_text(encoding='utf-8-sig'))
            if not isinstance(obj,dict):
                return
            clean={}
            for uid,value in obj.items():
                if uid.isascii() and uid.isdecimal() and isinstance(value,dict):
                    n,at=value.get('n'),value.get('at')
                    if isinstance(n,int) and 0<=n<=1000000 and isinstance(at,int) and at>0:
                        clean[uid]={'n':n,'at':at}
            with self.db:
                for uid,value in clean.items():
                    self.db.execute('''INSERT INTO shared_hints VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET
                        n=excluded.n,at_seconds=excluded.at_seconds WHERE excluded.at_seconds>=shared_hints.at_seconds''',
                        (uid,value['n'],value['at']))
            if path==live:
                atomic_write(saved,json.dumps(clean,ensure_ascii=False))
            self.shared_stamp=stamp
        except (OSError,ValueError):
            # A transient concurrent write is retried next pass; never clear previously read facts.
            return

    def status(self, row, now=None):
        if row['kind'] in ('unban','untimeout'):return ('Подтверждено снятие бана' if row['kind']=='unban' else 'Подтверждено снятие мута'),False
        now=int(time.time()*1000) if now is None else now
        user,channel,stamp=row['user'],row['channel'],row['at_ms']
        if row['kind']=='delete':
            later=self.db.execute('''SELECT kind,at_ms,duration FROM events WHERE user=? AND channel=?
                AND kind IN ('ban','timeout') AND at_ms>=? AND at_ms<=? ORDER BY at_ms LIMIT 1''',
                (user,channel,stamp,stamp+60000)).fetchone() if user else None
            if later:
                delay=max(0,(later['at_ms']-stamp)//1000)
                kind='бан' if later['kind']=='ban' else 'мут'
                return f'Через {delay} с зафиксирован {kind}',False
            return 'Удалено отдельно · наказание рядом не зафиксировано',False
        later=self.db.execute('''SELECT kind,at_ms FROM events WHERE user=? AND channel=?
            AND kind IN ('ban','timeout','unban','untimeout','speech')
            AND (at_ms>? OR (at_ms=? AND sequence>?)) ORDER BY at_ms,sequence LIMIT 1''',
            (user,channel,stamp,stamp,row['sequence'])).fetchone()
        if later:
            kind=later['kind']
            if kind=='speech': return 'Снят: пользователь снова пишет',False
            if kind=='unban': return 'Снят: событие разбана',False
            if kind=='untimeout': return 'Снят: событие снятия мута',False
            return 'Заменён новым наказанием',False
        if row['kind']=='timeout' and row['duration'] is not None and (row['last_at_ms'] or stamp)+row['duration']*1000<=now:
            return 'Мут истёк',False
        if row['kind']=='ban': return ('Chatterino+: снятие не подтверждено',False) if row['basis']=='shared_service' else ('Бан: снятие не замечено',True)
        return ('Мут: срок неизвестен' if row['duration'] is None else 'Мут действует'),True

    def totals(self,user=''):
        sql="SELECT kind,count(*) FROM events WHERE kind IN ('ban','timeout','delete')"
        args=[]
        if user:sql+=' AND user=?';args.append(name(user))
        counts=dict(self.db.execute(sql+' GROUP BY kind',args).fetchall())
        return {'bans':counts.get('ban',0),'timeouts':counts.get('timeout',0),'deletions':counts.get('delete',0)}

    def users(self,search='',extra=(),limit=200):
        totals=self.db.execute("""SELECT user,sum(kind='ban') AS bans,sum(kind='timeout') AS timeouts,sum(kind='delete') AS deletions
            FROM events WHERE kind IN ('ban','timeout','delete') AND user<>'' AND instr(user,?)>0 GROUP BY user ORDER BY user""",
            (search.strip().lower().lstrip('@'),)).fetchall()
        people={r['user']:dict(r) for r in totals}
        for user in extra:
            user=name(user)
            if search.strip().lower().lstrip('@') in user:
                people.setdefault(user,{'user':user,'bans':0,'timeouts':0,'deletions':0})
        names=sorted(people)
        return {'total':len(names),'rows':[people[u] for u in names[:limit]]}

    def query(self, user='', channel='', kind='', offset=0, limit=100, now=None,exact_user=False):
        terms=["kind IN ('ban','timeout','delete')"]
        args=[]
        if user.strip():
            # Nickname search is literal and case-insensitive; no wildcard or SQL interpolation.
            terms.append('user=?' if exact_user else 'instr(user,?)>0');args.append(user.strip().lower().lstrip('@'))
        if channel:
            terms.append('channel=?');args.append(name(channel))
        if kind:
            if kind not in ('ban','timeout','delete'): raise ValueError('Invalid action filter')
            terms.append('kind=?');args.append(kind)
        where=' AND '.join(terms)
        counts=dict(self.db.execute('SELECT kind,count(*) FROM events WHERE '+where+' GROUP BY kind',args).fetchall())
        rows=[]
        for row in self.db.execute('SELECT * FROM events WHERE '+where+' ORDER BY at_ms DESC,sequence DESC LIMIT ? OFFSET ?',args+[limit,offset]):
            result=dict(row)
            result['status'],result['active']=self.status(row,now)
            result['context']=json.loads(row['context'])
            result['message']=json.loads(row['message']) if row['message'] else None
            identity=self.db.execute('SELECT user_id FROM identities WHERE user=?',(result['user'],)).fetchone()
            uid=identity[0] if identity else ''
            if uid:
                for message in [*result['context'],result['message']]:
                    if isinstance(message,dict) and not message.get('user_id'):message['user_id']=uid
            rows.append(self.evidence.enrich(self.combined.enrich(result)))
        hint=None
        if user.strip():
            hint=self.db.execute('''SELECT n,at_seconds FROM shared_hints h JOIN identities i ON h.user_id=i.user_id
                WHERE i.user=?''',(user.strip().lower().lstrip('@'),)).fetchone()
        channels=[r[0] for r in self.db.execute('SELECT DISTINCT channel FROM events ORDER BY channel')]
        invalid=self.db.execute('SELECT coalesce(sum(invalid),0) FROM ingestion').fetchone()
        return {'total':sum(counts.values()),'bans':counts.get('ban',0),'timeouts':counts.get('timeout',0),'deletions':counts.get('delete',0),
                'rows':rows,'channels':channels,'hint':dict(hint) if hint else None,'invalid':invalid[0] if invalid else 0}

    def close(self):
        self.db.close()
