"""Non-blocking 7TV downloads and a bounded cache of visible animated images."""
from __future__ import annotations
from collections import OrderedDict
import json
import os
import queue
import re
import threading
import time
import urllib.error
import uuid
from PySide6.QtCore import QObject,Signal,QTimer,QSize
from PySide6.QtGui import QImageReader,QImage,QMovie
from branding import VERSION
from seven_tv_store import EmoteStore,API,MAX_JSON,MAX_IMAGE,fetch,fetch_personal_users,parse_catalog,permitted_url

class NetworkPool:
    def __init__(self,fetcher=fetch):
        self.tasks=queue.PriorityQueue();self.results=queue.Queue();self.stop=threading.Event();self.fetcher=fetcher;self.sequence=0
        self.personal_tasks=queue.Queue()
        self.threads=[threading.Thread(target=self.run,daemon=True,name='7TV download') for _ in range(2)]
        for thread in self.threads:thread.start()
        self.personal_thread=threading.Thread(target=self.personal_run,daemon=True,name='7TV personal sets');self.personal_thread.start()
    def submit(self,key,url,channel='',uid=''):
        self.sequence+=1;self.tasks.put((0 if channel else 1,self.sequence,(key,url,channel,uid)))
    def submit_personal(self,key,uid):self.personal_tasks.put((key,uid))
    def personal_run(self):
        while not self.stop.is_set():
            try:first=self.personal_tasks.get(timeout=.3)
            except queue.Empty:continue
            batch=[first];deadline=time.monotonic()+.08
            while len(batch)<20:
                try:batch.append(self.personal_tasks.get(timeout=max(0,deadline-time.monotonic())))
                except queue.Empty:break
            try:
                catalogs=fetch_personal_users([uid for _,uid in batch])
                for key,uid in batch:self.results.put((key,catalogs[uid],None,0))
            except urllib.error.HTTPError as exc:
                try:retry=max(60,min(3600,int(exc.headers.get('Retry-After','60'))))
                except (ValueError,AttributeError):retry=60
                for key,_ in batch:self.results.put((key,None,'HTTP '+str(exc.code),retry))
            except Exception as exc:
                for key,_ in batch:self.results.put((key,None,str(exc),60))
    def run(self):
        while not self.stop.is_set():
            try:_,_,task=self.tasks.get(timeout=.3)
            except queue.Empty:continue
            key,url,channel,uid=task
            try:
                data=self.fetcher(url,MAX_JSON if channel else MAX_IMAGE)
                if channel:data=parse_catalog(json.loads(data),channel,uid)
                self.results.put((key,data,None,0))
            except urllib.error.HTTPError as exc:
                if channel and channel!='global' and exc.code==404:
                    self.results.put((key,parse_catalog({'emote_set':None},channel,uid),None,0))
                else:
                    try:retry=max(60,min(3600,int(exc.headers.get('Retry-After','60'))))
                    except (ValueError,AttributeError):retry=60
                    self.results.put((key,None,'HTTP '+str(exc.code),retry))
            except Exception as exc:self.results.put((key,None,str(exc),60))

def personal_event(payload):
    """Extract bounded IDs only; raw EventAPI payloads are never persisted."""
    if not isinstance(payload,dict) or payload.get('op')!=0:return []
    data=payload.get('d') or {};kind=data.get('type','');body=data.get('body') or {}
    if kind in ('entitlement.create','entitlement.delete'):
        obj=body.get('object') or {}
        if obj.get('kind')!='EMOTE_SET':return []
        sid=str(obj.get('ref_id',''));result=[]
        for connection in (obj.get('user') or {}).get('connections') or []:
            uid=str(connection.get('id','')) if isinstance(connection,dict) and connection.get('platform')=='TWITCH' else ''
            if re.fullmatch(r'[0-9]{1,25}',uid):result.append(('user',uid,sid))
        return result
    if isinstance(kind,str) and kind.startswith('emote_set.'):
        sid=str(body.get('id') or (body.get('object') or {}).get('id') or '')
        return [('set',sid,'')] if re.fullmatch(r'(?:[0-9a-f]{24}|[0-9A-HJKMNP-TV-Z]{26})',sid,re.I) else []
    return []

class PersonalEvents(threading.Thread):
    """Follow the channel-scoped 7TV events used for personal emotes."""
    def __init__(self):
        super().__init__(daemon=True,name='7TV personal events');self.stopped=threading.Event();self.results=queue.Queue()
        self.lock=threading.Lock();self.channels=();self.generation=0;self.phase='connecting';self.start()
    def set_channels(self,ids):
        value=tuple(sorted({str(uid) for uid in ids if re.fullmatch(r'[0-9]{1,25}',str(uid))}))
        with self.lock:
            if value!=self.channels:self.channels=value;self.generation+=1
    def snapshot(self):
        with self.lock:return self.channels,self.generation,self.phase
    def set_phase(self,value):
        with self.lock:self.phase=value
    def run(self):
        import websocket
        delay=2
        while not self.stopped.is_set():
            channels,generation,_=self.snapshot()
            if not channels:
                self.set_phase('waiting');self.stopped.wait(.5);continue
            ws=None
            try:
                self.set_phase('connecting')
                ws=websocket.create_connection('wss://events.7tv.io/v3?app=mist1x_archive&version='+VERSION,
                    timeout=8,origin='https://7tv.app',enable_multithread=True)
                hello=json.loads(ws.recv());heartbeat=max(1000,int((hello.get('d') or {}).get('heartbeat_interval',15000)))/1000
                ws.settimeout(min(2,heartbeat));last=time.monotonic()
                for uid in channels:
                    condition={'ctx':'channel','platform':'TWITCH','id':uid}
                    for event in ('entitlement.create','entitlement.delete','emote_set.*'):
                        ws.send(json.dumps({'op':35,'d':{'type':event,'condition':condition}},separators=(',',':')))
                self.set_phase('connected');self.results.put(('resync','',''));delay=2
                while not self.stopped.is_set():
                    if self.snapshot()[1]!=generation:break
                    try:payload=json.loads(ws.recv());last=time.monotonic()
                    except websocket.WebSocketTimeoutException:
                        if time.monotonic()-last>heartbeat*3:break
                        continue
                    for event in personal_event(payload):self.results.put(event)
                    if payload.get('op') in (4,7):break
            except Exception:self.set_phase('error')
            finally:
                if ws:
                    try:ws.close()
                    except Exception:pass
            if not self.stopped.is_set():self.stopped.wait(delay);delay=min(60,delay*2)
    def close(self):self.stopped.set()

class SevenTV(QObject):
    changed=Signal()
    frame=Signal(str)
    statusChanged=Signal()
    def __init__(self,data,settings,online=True,parent=None):
        super().__init__(parent);self.store=EmoteStore(data);self.settings=settings;self.online=online
        self.enabled=settings.get('seven_tv',True) is not False;self.motion=settings.get('seven_tv_animation',True) is not False
        self.pool=NetworkPool() if online else None;self.events=PersonalEvents() if online else None
        self.pending={};self.retry={};self.errors={};self.images=OrderedDict();self.seen_users=OrderedDict()
        self.downloaded=len(list(self.store.assets.glob('*.*')));self.closed=False;self.last_scan=0.;self.channels=[]
        self.timer=QTimer(self);self.timer.setInterval(120);self.timer.timeout.connect(self.poll);self.timer.start()
        self.housekeeping=QTimer(self);self.housekeeping.setInterval(1000);self.housekeeping.timeout.connect(self.maintain);self.housekeeping.start()
    def set_channels(self,entries,extra=()):
        self.channels=sorted(set(extra)|{e['name'] for e in entries if isinstance(e,dict) and isinstance(e.get('name'),str)})
        discovered=False
        for entry in entries:
            if not isinstance(entry,dict):continue
            ch=entry.get('name','');uid=str(entry.get('twitch_id',''))
            if re.fullmatch('[a-z0-9_]{1,64}',ch) and re.fullmatch('[0-9]{1,25}',uid):
                discovered=discovered or self.store.ids.get(ch)!=uid;self.store.ids[ch]=uid
        if self.events:self.events.set_channels(self.store.ids.get(ch,'') for ch in self.channels)
        if discovered or time.monotonic()-self.last_scan>30:self.refresh()
    def request(self,key,url,channel='',uid=''):
        if not self.online or not self.enabled or self.closed or key in self.pending or time.monotonic()<self.retry.get(key,0) or len(self.pending)>=1024:return
        if not permitted_url(url):return
        self.pending[key]=(url,channel,uid);self.pool.submit(key,url,channel,uid)
    def request_personal(self,uid,force=False):
        uid=str(uid)
        if not self.online or not self.enabled or self.closed or not re.fullmatch(r'[0-9]{1,25}',uid):return
        self.seen_users[uid]=time.monotonic();self.seen_users.move_to_end(uid)
        while len(self.seen_users)>5000:self.seen_users.popitem(last=False)
        cached=self.store.personal.get(uid)
        if not force and cached and time.time()-cached.get('observed_at',0)<900:return
        key='personal:'+uid
        if key in self.pending or time.monotonic()<self.retry.get(key,0) or len(self.pending)>=1024:return
        self.pending[key]=('', 'personal',uid);self.pool.submit_personal(key,uid);self.statusChanged.emit()
    def refresh(self,force=False):
        self.last_scan=time.monotonic()
        if not self.enabled:return
        for channel in ['global',*self.channels]:
            cached=self.store.catalogs.get(channel)
            if not force and cached and time.time()-cached.get('observed_at',0)<600:continue
            uid=self.store.ids.get(channel)
            if channel=='global':url=API+'/emote-sets/global'
            elif uid:url=API+'/users/twitch/'+uid
            else:continue
            self.request('catalog:'+channel,url,channel,uid or '')
        if force:
            for uid in list(self.seen_users)[-500:]:self.request_personal(uid,force=True)
        self.statusChanged.emit()
    def configure(self,enabled=None,motion=None):
        if enabled is not None:self.enabled=bool(enabled);self.settings['seven_tv']=self.enabled
        if motion is not None:self.motion=bool(motion);self.settings['seven_tv_animation']=self.motion
        for entry in self.images.values():
            movie=entry.get('movie')
            if movie:movie.setPaused(not (self.enabled and self.motion))
        if self.events:self.events.set_channels((self.store.ids.get(ch,'') for ch in self.channels) if self.enabled else ())
        if self.enabled:self.refresh()
        self.changed.emit();self.statusChanged.emit()
    def bind(self,row,slot='body'):
        if not self.enabled or self.closed:return {}
        self.request_personal(row.get('user_id',''))
        refs=self.store.bind(row,slot)
        for ref in refs.values():self.ensure(ref)
        return refs
    def path(self,ref):
        key=ref.get('key','');filename=ref.get('file','')
        if not re.fullmatch('[a-f0-9]{64}',key) or not re.fullmatch(re.escape(key)+r'\.(webp|gif|png)',filename):return None
        return self.store.assets/filename
    def ensure(self,ref):
        path=self.path(ref)
        if path is None:return
        if not path.is_file():self.request('asset:'+ref['key'],ref.get('url',''))
    def image(self,ref):
        """Called only when a visible item needs a frame; off-screen movies are paused."""
        if not self.enabled or self.closed:return None
        key=ref.get('key','');entry=self.images.get(key)
        if entry:
            entry['used']=time.monotonic();self.images.move_to_end(key)
            movie=entry.get('movie')
            if movie:
                if self.motion:movie.setPaused(False)
                return movie.currentImage() if self.motion else entry['first']
            return entry['first']
        path=self.path(ref)
        if path is None:return None
        if not path.is_file():self.ensure(ref);return None
        reader=QImageReader(str(path));size=reader.size()
        if size.width()<1 or size.height()<1 or size.width()>2048 or size.height()>512 or size.width()*size.height()>524288:return None
        image=reader.read()
        if image.isNull():return None
        entry={'first':image,'used':time.monotonic(),'movie':None}
        if ref.get('animated'):
            movie=QMovie(str(path));movie.setCacheMode(QMovie.CacheNone)
            if movie.isValid():
                movie.frameChanged.connect(lambda index,k=key:self.frame.emit(k));entry['movie']=movie;movie.start()
                if not self.motion:movie.setPaused(True)
        self.images[key]=entry
        while len(self.images)>128:
            _,old=self.images.popitem(last=False)
            if old['movie']:old['movie'].stop();old['movie'].deleteLater()
        return entry['movie'].currentImage() if entry['movie'] and self.motion else image
    def poll(self):
        if not self.pool or self.closed:return
        changed=False
        if self.events:
            for _ in range(100):
                try:kind,value,extra=self.events.results.get_nowait()
                except queue.Empty:break
                if kind=='user':self.request_personal(value,force=True)
                elif kind=='set':
                    for uid in self.store.users_for_set(value):self.request_personal(uid,force=True)
                elif kind=='resync':
                    for uid in list(self.seen_users)[-500:]:self.request_personal(uid,force=True)
        for _ in range(20):
            try:key,data,error,retry=self.pool.results.get_nowait()
            except queue.Empty:break
            request=self.pending.pop(key,None)
            if request is None:continue
            if error:
                self.errors[key]=error;self.retry[key]=time.monotonic()+retry;continue
            try:
                if key.startswith('catalog:'):self.store.install(data)
                elif key.startswith('personal:'):self.store.install_personal(data)
                else:
                    url=request[0];filename=key.split(':',1)[1]+'.'+url.rsplit('.',1)[1]
                    target=self.store.assets/filename;temp=target.with_suffix(target.suffix+'.'+uuid.uuid4().hex+'.tmp')
                    try:
                        with temp.open('wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
                        os.replace(temp,target)
                    finally:temp.unlink(missing_ok=True)
                    self.downloaded+=1
                self.errors.pop(key,None);self.retry.pop(key,None);changed=True
            except (OSError,ValueError) as exc:self.errors[key]=str(exc);self.retry[key]=time.monotonic()+60
        if changed:self.changed.emit()
        self.statusChanged.emit()
    def maintain(self):
        try:self.store.flush()
        except OSError:pass
        now=time.monotonic()
        for entry in self.images.values():
            if entry.get('movie') and (not self.motion or not self.enabled or now-entry['used']>2):entry['movie'].setPaused(True)
        if now-self.last_scan>30:self.refresh()
    def status_text(self):
        if not self.enabled:return '7TV выключен'
        ready=sum(ch in self.store.catalogs for ch in self.channels)
        personal=len(self.store.personal);event=('события подключены' if self.events and self.events.snapshot()[2]=='connected' else 'события переподключаются') if self.events else 'офлайн'
        if any(k.startswith('catalog:') for k in self.pending):return f'7TV · загружаем наборы каналов · {ready}/{len(self.channels)} · Personal: {personal}'
        if self.errors:return f'7TV · часть загрузок недоступна · сохранённые эмоуты доступны ({self.downloaded})'
        return f'7TV · каналы: {ready}/{len(self.channels)} · Personal: {personal} · {event} · изображений: {self.downloaded}'
    def close(self):
        if self.closed:return
        self.closed=True;self.timer.stop();self.housekeeping.stop()
        if self.pool:self.pool.stop.set()
        if self.events:self.events.close()
        for entry in self.images.values():
            if entry.get('movie'):entry['movie'].stop()
        self.store.close()
