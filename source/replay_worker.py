"""Stream discovery and SQLite writer; the IRC socket never waits on metadata or media."""
from collections import deque
import json
from pathlib import Path
import queue
import shutil
import sqlite3
import threading
import time
from replay_store import Session, settings_load, settings_save, sessions, session_key, session_path, milliseconds
from replay_media import helix, CaptureMedia
from storage import atomic_write, name

class ReplayWorker(threading.Thread):
    def __init__(self,data,token=lambda:None,streams_api=helix):
        super().__init__(daemon=True);self.data=Path(data);self.token=token;self.api=streams_api
        self.config=settings_load(data);self.lock=threading.Lock();self.events=queue.Queue(maxsize=100000)
        settings_save(data,self.config)
        self.commands=queue.Queue();self.results=queue.Queue();self.stopped=threading.Event()
        self.active={};self.pending=deque(maxlen=100000);self.next_check=0.;self.last_flush=0.;self.last_catalog={}
        self.media=CaptureMedia(data);self.state={'text':'Ожидание Twitch','active':[],'dropped':0};self.connected=set();self.gaps=set();self.last_dropped=0
    def wanted(self):
        with self.lock:return list(self.config['channels']) if self.config['enabled'] else []
    def snapshot(self):
        with self.lock:return dict(self.state)
    def report(self,text):
        with self.lock:self.state.update(text=text,active=sorted(self.active),pending_media=len(self.media.pending),media_errors=self.media.errors)
    def report_connection(self):
        live=set(self.active)&self.connected
        text='Записывается: '+', '.join('#'+x for x in sorted(live)) if live else ('Ожидаем подключение к чату Twitch' if self.active else 'Ожидаем эфиры выбранных каналов')
        self.report(text)
    def configure(self,config):
        settings_save(self.data,config)
        with self.lock:self.config=dict(config)
        self.commands.put(('config',None))
    def connection(self,channels):
        self.commands.put(('connection',set(channels)))
    def push(self,event,tags=None):
        if event['channel'] not in self.wanted():return
        try:self.events.put_nowait((dict(event),dict(tags or {})))
        except queue.Full:
            with self.lock:self.state['dropped']+=1
    def discover(self,now):
        wanted=self.wanted();token=self.token()
        if not wanted or not token:return
        streams=self.api('streams',{'user_login':wanted,'first':100},token)
        by_channel={name(s['user_login']):s for s in streams if s.get('type')=='live' and s.get('id') and s.get('started_at')}
        retired=[]
        for ch in list(self.active):
            s=by_channel.get(ch)
            if ch not in wanted or not s or str(s['id'])!=self.active[ch].meta['stream_id']:
                old=self.active.pop(ch)
                if s and ch in wanted:retired.append((old,milliseconds(s['started_at'])))
                else:old.close('finished' if ch in wanted else 'paused')
                self.gaps.discard(ch)
        for ch,s in by_channel.items():
            if ch not in wanted or session_key(ch,s['id']) in self.config['deleted']:continue
            if ch not in self.active:
                session=Session(self.data,s,now);self.active[ch]=session
                for (ref,) in session.db.execute('SELECT ref FROM assets'):
                    self.media.asset(session.folder,json.loads(ref))
                for (payload,) in session.db.execute("SELECT payload FROM events WHERE kind='message' AND instr(payload,'\"_personal_pending\"')>0"):
                    try:
                        for uid in json.loads(payload).get('_personal_pending',[]):
                            if str(uid).isdigit():self.media.submit(('personal',str(uid)),('personal',str(uid)))
                    except (ValueError,TypeError):pass
                session.append({'kind':'gap','at':now,'channel':ch,'text':'Запись подключена. Ранее полученные сообщения сохранены; полнота до этого момента не гарантируется.'})
            if time.monotonic()-self.last_catalog.get(ch,-1000)>600:
                self.media.catalog(ch,str(s['user_id']),token);self.last_catalog[ch]=time.monotonic()
        for event,tags in list(self.pending):
            if event['at']>=now-60000:self.record(event,tags)
        # On a new stream ID, move the overlap already received during metadata polling.
        # Copy first, commit, then remove it from the preceding broadcast.
        for old,boundary in retired:
            new=self.active.get(old.meta['channel'])
            if new:
                for (payload,) in old.db.execute('SELECT payload FROM events WHERE at>=?',(boundary,)).fetchall():
                    event=json.loads(payload);new.append(event)
                    for ref in [*event.get('emote_refs',{}).values(),*event.get('badge_refs',[]),*event.get('reply',{}).get('emote_refs',{}).values()]:self.media.asset(new.folder,ref)
                new.flush()
                old.db.execute('DELETE FROM events WHERE at>=?',(boundary,));old.db.commit()
                old.count=old.db.execute("SELECT count(*) FROM events WHERE kind='message'").fetchone()[0]
                old.meta['last_recorded']=min(old.meta['last_recorded'],boundary)
            old.close('finished')
        self.pending=deque(((e,t) for e,t in self.pending if e['at']>=now-60000),maxlen=100000)
        self.report_connection()
    def record(self,event,tags):
        session=self.active.get(event['channel'])
        if not session or event['channel'] not in self.wanted():return
        if event['kind']=='message':self.media.freeze(event,tags,session.folder)
        session.append(event)
    def process_media(self):
        refreshed=set()
        for _ in range(20):
            try:kind,value=self.media.results.get_nowait()
            except queue.Empty:break
            if kind=='personal':refreshed.update(value)
        if not refreshed:return
        for session in self.active.values():
            rows=session.db.execute("SELECT seq,payload FROM events WHERE kind='message' AND instr(payload,'\"_personal_pending\"')>0").fetchall()
            for seq,payload in rows:
                try:event=json.loads(payload);waiting=set(event.get('_personal_pending',[]))&refreshed
                except (ValueError,TypeError):continue
                if not waiting:continue
                for uid in waiting:self.media.apply_personal(event,uid,session.folder)
                session.replace(seq,event)
    def delete(self,key):
        folder=session_path(self.data,key)
        for ch,s in list(self.active.items()):
            if s.key==key:s.close('deleted');del self.active[ch]
        with self.lock:
            self.config['deleted']=sorted(set(self.config['deleted']+[key]));settings_save(self.data,self.config)
        # Asset worker may be writing the same folder. Wait for it before removing.
        with self.media.files_lock:
            self.media.cancelled.add(str(folder))
            if folder.resolve().parent!=(self.data/'replays/streams').resolve():raise ValueError('Запись находится вне папки повторов.')
            if folder.is_symlink() or (hasattr(folder,'is_junction') and folder.is_junction()) or any(p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()) for p in folder.rglob('*')):raise ValueError('Запись содержит ссылку на другую папку.')
            for attempt in range(10):
                try:
                    if folder.exists():shutil.rmtree(folder)
                    break
                except OSError:
                    if attempt==9:raise
                    time.sleep(.1)
        self.results.put(('deleted',key))
    def process_commands(self,now):
        while True:
            try:kind,value=self.commands.get_nowait()
            except queue.Empty:break
            if kind=='config':
                self.next_check=0
                for ch in list(self.active):
                    if ch not in self.wanted():self.active.pop(ch).close('paused')
                self.pending=deque(((e,t) for e,t in self.pending if e['channel'] in self.wanted()),maxlen=100000)
            elif kind=='connection':
                for ch,s in self.active.items():
                    if ch not in value and ch not in self.gaps:
                        s.append({'kind':'gap','at':now,'channel':ch,'text':'Соединение прервано: возможен пропуск сообщений.'});self.gaps.add(ch)
                    elif ch in value and ch in self.gaps:
                        s.append({'kind':'gap','at':now,'channel':ch,'text':'Соединение восстановлено.'});self.gaps.discard(ch)
                self.connected=value
                self.report_connection()
            elif kind=='delete':self.delete(value)
    def run(self):
        self.media.start()
        try:
            # Pending downloads also survive when the original broadcast has ended.
            for meta in sessions(self.data):
                folder=session_path(self.data,meta['key'])
                try:
                    db=sqlite3.connect((folder/'chat.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
                    try:
                        for (ref,) in db.execute('SELECT ref FROM assets'):self.media.asset(folder,json.loads(ref))
                    finally:db.close()
                except (OSError,ValueError,sqlite3.Error):continue
            while not self.stopped.is_set():
                now=int(time.time()*1000)
                try:
                    self.process_commands(now)
                    self.process_media()
                    if time.monotonic()>=self.next_check:
                        self.next_check=time.monotonic()+30
                        try:self.discover(now)
                        except Exception:
                            self.report('Не удалось проверить эфиры. Запись подтверждённых эфиров продолжается; границы будут уточнены после подключения.')
                    for _ in range(3000):
                        try:event,tags=self.events.get_nowait()
                        except queue.Empty:break
                        if event['channel'] in self.wanted():
                            if event['channel'] in self.active:self.record(event,tags)
                            self.pending.append((event,tags))
                    if time.monotonic()-self.last_flush>=1:
                        dropped=self.snapshot()['dropped']
                        if dropped>self.last_dropped:
                            for ch,s in self.active.items():s.append({'kind':'gap','at':now,'channel':ch,'text':'Перегрузка очереди записи: часть сообщений могла быть пропущена.'})
                            self.last_dropped=dropped
                        for session in self.active.values():session.flush()
                        atomic_write(self.data/'replays/status.json',json.dumps({**self.snapshot(),'updated_at':time.time()},ensure_ascii=False))
                        self.last_flush=time.monotonic()
                except Exception as exc:
                    self.report('Ошибка записи повторов: '+str(exc)[:150]);self.results.put(('error','Ошибка записи повторов. Проверь свободное место.'))
                self.stopped.wait(.1)
        finally:
            while True:
                try:event,tags=self.events.get_nowait()
                except queue.Empty:break
                self.record(event,tags)
            self.process_media()
            for session in self.active.values():session.close('paused')
            self.media.stopped.set();self.media.join(timeout=7)
