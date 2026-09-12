"""Durable per-broadcast databases. No account secrets, no dependency on Chatterino."""
import datetime as dt
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from storage import atomic_write, name

DEFAULT_CHANNELS = []

def milliseconds(value):
    return int(dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()*1000)

def utc(value):
    return dt.datetime.fromtimestamp(value/1000, dt.timezone.utc).isoformat(timespec='milliseconds')

def settings_load(data):
    path=Path(data)/'replays/settings.json'
    if not path.exists():return {'enabled':True,'channels':list(DEFAULT_CHANNELS),'deleted':[]}
    raw=json.loads(path.read_text(encoding='utf-8'))
    channels=sorted({name(x) for x in raw.get('channels',[])})
    if len(channels)>100:raise ValueError('Можно записывать до 100 каналов.')
    return {'enabled':raw.get('enabled') is True,'channels':channels,
            'deleted':[x for x in raw.get('deleted',[]) if isinstance(x,str) and re.fullmatch('[a-f0-9]{64}',x)]}

def settings_save(data, value):
    channels=sorted({name(x) for x in value['channels']})
    if len(channels)>100:raise ValueError('Можно записывать до 100 каналов.')
    atomic_write(Path(data)/'replays/settings.json',json.dumps({**value,'channels':channels},ensure_ascii=False,indent=2))

def session_key(channel, stream_id):
    return hashlib.sha256((name(channel)+'|'+str(stream_id)).encode()).hexdigest()

def session_path(data,key):
    if not re.fullmatch('[a-f0-9]{64}',key):raise ValueError('Неизвестная запись.')
    return Path(data)/'replays/streams'/key

class Session:
    def __init__(self,data,stream,now):
        channel=name(stream['user_login']);self.key=session_key(channel,stream['id'])
        self.folder=session_path(data,self.key);self.folder.mkdir(parents=True,exist_ok=True)
        self.path=self.folder/'chat.sqlite3'
        self.db=sqlite3.connect(self.path,timeout=4)
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''CREATE TABLE IF NOT EXISTS events(
          seq INTEGER PRIMARY KEY, key TEXT UNIQUE, at INTEGER NOT NULL,kind TEXT NOT NULL,
          user TEXT NOT NULL,msgid TEXT NOT NULL,text TEXT NOT NULL,payload TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS by_time ON events(at,seq);
          CREATE INDEX IF NOT EXISTS by_user ON events(user,at);
          CREATE INDEX IF NOT EXISTS by_kind ON events(kind,at);
          CREATE INDEX IF NOT EXISTS by_msg ON events(msgid);
          CREATE TABLE IF NOT EXISTS assets(key TEXT PRIMARY KEY,ref TEXT NOT NULL);
        ''')
        old={}
        if (self.folder/'stream.json').exists():old=json.loads((self.folder/'stream.json').read_text(encoding='utf-8'))
        self.meta={**old,'key':self.key,'channel':channel,'stream_id':str(stream['id']),
          'title':stream.get('title',''),'started_at':milliseconds(stream['started_at']),
          'first_recorded':old.get('first_recorded',now),'last_recorded':old.get('last_recorded',now),
          'state':'recording','channel_id':stream.get('user_id','')}
        self.count=self.db.execute("SELECT count(*) FROM events WHERE kind='message'").fetchone()[0]
        self.dirty=0;self.save_meta()
    def save_meta(self):
        self.meta['messages']=self.count
        atomic_write(self.folder/'stream.json',json.dumps(self.meta,ensure_ascii=False,indent=2))
    def append(self,event):
        event=dict(event);at=int(event['at'])
        if at<self.meta['started_at']:return False
        kind=event['kind'];user=event.get('user','');mid=event.get('id','')
        key=event.get('event_key') or (('msg:'+mid) if kind=='message' else hashlib.sha256(json.dumps([kind,at,user,mid,event.get('duration')]).encode()).hexdigest())
        row=self.db.execute('INSERT OR IGNORE INTO events(key,at,kind,user,msgid,text,payload) VALUES(?,?,?,?,?,?,?)',
          (key,at,kind,user,mid,event.get('text',''),json.dumps(event,ensure_ascii=False,separators=(',',':'))))
        if not row.rowcount:return False
        for ref in [*event.get('emote_refs',{}).values(),*event.get('badge_refs',[]),*event.get('reply',{}).get('emote_refs',{}).values()]:
            self.db.execute('INSERT OR IGNORE INTO assets VALUES(?,?)',(ref['key'],json.dumps(ref,ensure_ascii=False)))
        self.dirty+=1
        if kind=='message':self.count+=1
        self.meta['last_recorded']=max(at,self.meta['last_recorded'])
        return int(row.lastrowid)
    def replace(self,seq,event):
        """Replace one event after delayed metadata arrives, on the writer thread."""
        payload=json.dumps(event,ensure_ascii=False,separators=(',',':'))
        row=self.db.execute('UPDATE events SET text=?,payload=? WHERE seq=?',(event.get('text',''),payload,int(seq)))
        if not row.rowcount:return False
        for ref in [*event.get('emote_refs',{}).values(),*event.get('badge_refs',[]),*event.get('reply',{}).get('emote_refs',{}).values()]:
            self.db.execute('INSERT OR IGNORE INTO assets VALUES(?,?)',(ref['key'],json.dumps(ref,ensure_ascii=False)))
        self.dirty+=1
        return True
    def flush(self):
        if self.dirty:self.db.commit();self.dirty=0;self.save_meta()
    def close(self,state='paused'):
        self.flush();self.meta['state']=state;self.save_meta()
        self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)');self.db.close()

def sessions(data):
    result=[]
    for p in (Path(data)/'replays/streams').glob('*/stream.json'):
        try:
            meta=json.loads(p.read_text(encoding='utf-8'))
            if p.parent!=session_path(data,meta['key']):continue
            meta['bytes']=sum(x.stat().st_size for x in p.parent.rglob('*') if x.is_file())
            result.append(meta)
        except (OSError,ValueError,KeyError):continue
    return sorted(result,key=lambda x:x['started_at'],reverse=True)

class Reader:
    def __init__(self,data,key):
        folder=session_path(data,key)
        self.meta=json.loads((folder/'stream.json').read_text(encoding='utf-8'))
        self.db=sqlite3.connect((folder/'chat.sqlite3').resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
        self.db.row_factory=sqlite3.Row
        self.db.create_function('casefold',1,lambda s:s.casefold())
    def close(self):self.db.close()
    def bounds(self):
        row=self.db.execute('SELECT min(at),max(at),count(*) FROM events').fetchone()
        return row[0] or self.meta['first_recorded'],row[1] or self.meta['last_recorded'],row[2]
    def page(self,at,mode='before',limit=250):
        op,order=('<=','DESC') if mode=='before' else ('>=','ASC')
        rows=self.db.execute(f'SELECT seq,payload FROM events WHERE at {op} ? ORDER BY at {order},seq {order} LIMIT ?', (at,limit)).fetchall()
        result=[dict(json.loads(r['payload']),seq=r['seq']) for r in rows]
        return list(reversed(result)) if mode=='before' else result
    def adjacent(self,at,seq,direction,limit=250):
        op,order=('<','DESC') if direction<0 else ('>','ASC')
        rows=self.db.execute(f'SELECT seq,payload FROM events WHERE (at,seq) {op} (?,?) ORDER BY at {order},seq {order} LIMIT ?',(at,seq,limit)).fetchall()
        result=[dict(json.loads(r['payload']),seq=r['seq']) for r in rows]
        return list(reversed(result)) if direction<0 else result
    def search(self,text,user='',after=0,limit=100):
        # Literal substring search; no executable SQL or wildcard surprises.
        rows=self.db.execute("SELECT seq,payload FROM events WHERE kind='message' AND seq>? AND (?='' OR user=?) AND instr(casefold(text),casefold(?))>0 ORDER BY seq LIMIT ?",(after,user,user,text,limit)).fetchall()
        return [dict(json.loads(r['payload']),seq=r['seq']) for r in rows]
    def dimmed(self,rows,cutoff):
        messages=[row for row in rows if row['kind']=='message']
        if not messages:return set()
        events=self.db.execute("SELECT kind,at,seq,user,msgid FROM events WHERE kind IN ('delete','ban','timeout','clear') AND at>=? AND at<=?",(min(r['at'] for r in messages),cutoff))
        users={};deleted={};cleared=(-1,-1)
        for event in events:
            stamp=(event['at'],event['seq'])
            if event['kind']=='clear':cleared=max(cleared,stamp)
            else:
                target=deleted if event['kind']=='delete' else users
                key=event['msgid'] if event['kind']=='delete' else event['user']
                target[key]=max(target.get(key,(-1,-1)),stamp)
        return {r['seq'] for r in messages if max(cleared,users.get(r.get('user',''),(-1,-1)),deleted.get(r.get('id',''),(-1,-1)))>=(r['at'],r['seq'])}

    def context(self,at,seq):
        return self.adjacent(at,seq+1,-1,100)+self.adjacent(at,seq,1,150)
