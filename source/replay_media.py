"""Freeze emote aliases and badges when recording, download on a separate thread."""
import hashlib
import json
import queue
import re
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urlencode
from seven_tv_store import permitted_url, fetch, fetch_personal_users, parse_catalog, MAX_IMAGE, MAX_JSON
from storage import atomic_write
from twitch_core import CLIENT_ID, TwitchError

def helix(path,fields,token):
    if path not in ('streams','chat/badges','chat/badges/global'):raise ValueError('Unexpected Twitch endpoint')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    req=urllib.request.Request('https://api.twitch.tv/helix/'+path+'?'+urlencode(fields,doseq=True),headers={
        'Authorization':'Bearer '+token,'Client-Id':CLIENT_ID,'Accept':'application/json'})
    try:
        with urllib.request.build_opener(NoRedirect).open(req,timeout=6) as r:
            content=r.read(MAX_JSON+1)
            if len(content)>MAX_JSON:raise ValueError('Oversize response')
            result=json.loads(content)
            if not isinstance(result.get('data'),list):raise ValueError('Invalid Twitch response')
            return result['data']
    except Exception:raise TwitchError('network','Не удалось проверить состояние эфира Twitch.') from None

def media_url(url):
    if permitted_url(url):return True
    try:p=urlsplit(url)
    except (ValueError,TypeError):return False
    return (p.scheme=='https' and not p.username and not p.password and p.port in (None,443)
        and not p.query and not p.fragment and
        ((p.hostname=='static-cdn.jtvnw.net' and re.fullmatch(r'/emoticons/v2/[A-Za-z0-9_-]+/default/dark/3\.0',p.path))
         or (p.hostname=='static-cdn.jtvnw.net' and re.fullmatch(r'/badges/v1/[a-f0-9-]+/3',p.path))))

def reference(url,label,animated=False):
    if not media_url(url):return None
    key=hashlib.sha256(url.encode()).hexdigest()
    return {'key':key,'file':key+'.webp','url':url,'name':label,'animated':animated,'zero_width':False}

class CaptureMedia(threading.Thread):
    def __init__(self,data):
        super().__init__(daemon=True);self.data=Path(data);self.tasks=queue.Queue(maxsize=30000)
        self.stopped=threading.Event();self.lock=threading.Lock();self.files_lock=threading.Lock();self.catalogs={};self.personal={};self.badges={};self.pending=set();self.results=queue.Queue();self.errors=0;self.cancelled=set()
        for p in (self.data/'7tv/catalogs').glob('*.json'):
            try:self.catalogs[p.stem]=json.loads(p.read_text(encoding='utf-8'))['emotes']
            except (OSError,ValueError,KeyError):pass
        for p in (self.data/'7tv/personal').glob('*.json'):
            try:
                value=json.loads(p.read_text(encoding='utf-8'))
                if p.stem.isdigit() and value.get('twitch_id')==p.stem:self.personal[p.stem]=value
            except (OSError,ValueError,KeyError):pass
    def catalog(self,ch,uid,token):
        self.submit(('catalog',ch,uid,token),('catalog',ch))
    def submit(self,job,key):
        with self.lock:
            if key in self.pending:return
            try:self.tasks.put_nowait((key,job));self.pending.add(key)
            except queue.Full:self.errors+=1
    def asset(self,folder,ref):
        if not ref:return
        if (folder/'assets'/ref['file']).is_file():return
        self.submit(('asset',folder,dict(ref)),('asset',str(folder),ref['key']))
    def freeze(self,record,tags,folder):
        record.pop('_personal_pending',None)
        uid=str(record.get('user_id',''))
        with self.lock:
            base={**self.catalogs.get('global',{}),**self.catalogs.get(record['channel'],{})}
            personal=self.personal.get(uid)
            badges={**self.badges.get('global',{}),**self.badges.get(record['channel'],{})}
        pending=[]
        if uid.isdigit() and (not personal or time.time()-personal.get('observed_at',0)>900):
            self.submit(('personal',uid),('personal',uid));pending.append(uid)
        refs={} if uid.isdigit() and not personal else {t:dict(base[t]) for t in re.findall(r'\S+',record['text']) if t in base}
        # IRC ranges refer to the unmodified message, including ACTION wrapping.
        raw=record.get('raw_text',record['text'])
        for definition in tags.get('emotes','').split('/'):
            eid,sep,ranges=definition.partition(':')
            if not sep or not re.fullmatch('[A-Za-z0-9_-]+',eid):continue
            for r in ranges.split(','):
                try:a,b=map(int,r.split('-'));alias=raw[a:b+1]
                except ValueError:continue
                if alias and 0<=a<=b<len(raw):refs[alias]=reference('https://static-cdn.jtvnw.net/emoticons/v2/'+eid+'/default/dark/3.0',alias,True)
        # A personal alias is the user's explicit choice and wins over channel
        # and global 7TV aliases (and over a same-text native emote range).
        if personal:
            owned=personal.get('emotes',{})
            refs.update({t:dict(owned[t]) for t in re.findall(r'\S+',record['text']) if t in owned})
        record['emote_refs']={k:v for k,v in refs.items() if v}
        record['badge_refs']=[badges[b] for b in tags.get('badges','').split(',') if b in badges]
        if record.get('reply'):
            reply=record['reply'];reply_uid=str(reply.get('user_id',''))
            with self.lock:reply_personal=self.personal.get(reply_uid)
            if reply_uid.isdigit() and (not reply_personal or time.time()-reply_personal.get('observed_at',0)>900):
                self.submit(('personal',reply_uid),('personal',reply_uid));pending.append(reply_uid)
            reply_catalog={} if reply_uid.isdigit() and not reply_personal else dict(base)
            if reply_personal:reply_catalog.update(reply_personal.get('emotes',{}))
            reply['emote_refs']={t:dict(reply_catalog[t]) for t in re.findall(r'\S+',reply.get('text','')) if t in reply_catalog}
        if pending:record['_personal_pending']=sorted(set(pending))
        for ref in [*record['emote_refs'].values(),*record['badge_refs'],*record.get('reply',{}).get('emote_refs',{}).values()]:self.asset(folder,ref)
        record.pop('raw_text',None)
    def apply_personal(self,record,uid,folder):
        """Correct a frozen row after a delayed Personal Emotes lookup."""
        with self.lock:
            base={**self.catalogs.get('global',{}),**self.catalogs.get(record.get('channel',''),{})}
            owned=self.personal.get(uid,{}).get('emotes',{})
        def refresh(target,text):
            refs=dict(target.get('emote_refs',{}));removed=[]
            for alias,ref in list(refs.items()):
                if ref.get('personal') and ref.get('owner_twitch_id')==uid:
                    removed.append(alias);refs.pop(alias,None)
            for alias in removed:
                if alias in base:refs[alias]=dict(base[alias])
            for token in re.findall(r'\S+',text):
                if token in base:refs.setdefault(token,dict(base[token]))
            refs.update({token:dict(owned[token]) for token in re.findall(r'\S+',text) if token in owned})
            target['emote_refs']=refs
        if str(record.get('user_id',''))==uid:refresh(record,record.get('text',''))
        reply=record.get('reply') or {}
        if str(reply.get('user_id',''))==uid:refresh(reply,reply.get('text',''))
        waiting=[item for item in record.get('_personal_pending',[]) if item!=uid]
        if waiting:record['_personal_pending']=waiting
        else:record.pop('_personal_pending',None)
        for ref in [*record.get('emote_refs',{}).values(),*reply.get('emote_refs',{}).values()]:self.asset(folder,ref)
    def run(self):
        while not self.stopped.is_set():
            try:key,job=self.tasks.get(timeout=.2)
            except queue.Empty:continue
            try:
                if job[0]=='personal':
                    batch=[(key,job)];deferred=[]
                    while len(batch)<20:
                        try:item=self.tasks.get_nowait()
                        except queue.Empty:break
                        if item[1][0]=='personal':batch.append(item)
                        else:deferred.append(item)
                    for item in deferred:self.tasks.put_nowait(item)
                    try:
                        uids=[item[1][1] for item in batch];catalogs=fetch_personal_users(uids)
                        folder=self.data/'7tv/personal';folder.mkdir(parents=True,exist_ok=True)
                        with self.lock:
                            for uid in uids:self.personal[uid]=catalogs[uid]
                        for uid in uids:atomic_write(folder/(uid+'.json'),json.dumps(catalogs[uid],ensure_ascii=False,separators=(',',':')))
                        self.results.put(('personal',tuple(uids)))
                    except Exception:
                        with self.lock:self.errors+=len(batch)
                    finally:
                        with self.lock:
                            for pending_key,_ in batch:self.pending.discard(pending_key)
                    continue
                if job[0]=='catalog':
                    _,ch,uid,token=job
                    for channel in ['global',ch]:
                        if self.stopped.is_set():break
                        url='https://7tv.io/v3/emote-sets/global' if channel=='global' else 'https://7tv.io/v3/users/twitch/'+uid
                        try:
                            value=parse_catalog(json.loads(fetch(url,MAX_JSON)),channel,uid)['emotes']
                            with self.lock:self.catalogs[channel]=value
                        except Exception:pass
                        try:
                            sets=helix('chat/badges/global' if channel=='global' else 'chat/badges',{} if channel=='global' else {'broadcaster_id':uid},token)
                            value={}
                            for group in sets:
                                for v in group.get('versions',[]):
                                    ref=reference(v.get('image_url_4x',''),v.get('title',group['set_id']))
                                    if ref:value[group['set_id']+'/'+v['id']]=ref
                            with self.lock:self.badges[channel]=value
                        except Exception:pass
                else:
                    _,folder,ref=job;url=ref['url']
                    if not media_url(url):continue
                    if permitted_url(url):payload=fetch(url,MAX_IMAGE)
                    else:
                        class NoRedirect(urllib.request.HTTPRedirectHandler):
                            def redirect_request(self,*args,**kwargs):return None
                        with urllib.request.build_opener(NoRedirect).open(url,timeout=6) as r:payload=r.read(MAX_IMAGE+1)
                    if not payload or len(payload)>MAX_IMAGE:continue
                    # Deleted sessions must never be recreated by pending downloads.
                    with self.files_lock:
                        if self.stopped.is_set() or str(folder) in self.cancelled or not (folder/'stream.json').exists():continue
                        assets=folder/'assets';assets.mkdir(exist_ok=True)
                        target=assets/ref['file'];temp=target.with_suffix('.tmp')
                        temp.write_bytes(payload);temp.replace(target)
            except Exception:
                with self.lock:self.errors+=1
            finally:
                with self.lock:self.pending.discard(key)

def make_view_media(data,folder,settings,parent):
    from seven_tv import SevenTV
    class RecordedMedia(SevenTV):
        def __init__(self,*args,**kwargs):
            from collections import OrderedDict
            self.waiting_files={};self.measure_images=OrderedDict()
            super().__init__(*args,**kwargs)
        def measure_image(self,ref):
            # Document layout measures off-screen objects too. This must not start
            # or resume their movies; decoding animation is reserved for painting.
            if not self.enabled or self.closed:return None
            key=ref['key']
            if key in self.images:return self.images[key]['first']
            if key in self.measure_images:
                self.measure_images.move_to_end(key);return self.measure_images[key]
            path=self.path(ref)
            if path is None:return None
            if not path.is_file():self.waiting_files[key]=path;return None
            from PySide6.QtGui import QImageReader
            reader=QImageReader(str(path));size=reader.size()
            if size.width()<1 or size.height()<1 or size.width()>2048 or size.height()>512 or size.width()*size.height()>524288:return None
            picture=reader.read()
            if picture.isNull():return None
            self.measure_images[key]=picture
            while len(self.measure_images)>256:self.measure_images.popitem(last=False)
            return picture
        def bind(self,row,slot='body'):return row.get('emote_refs',{}) if self.enabled else {}
        def image(self,ref):
            if ref.get('key') in self.images:return super().image(ref)
            path=self.path(ref)
            if path is not None and not path.is_file():self.waiting_files[ref['key']]=path
            return super().image(ref)
        def poll(self):
            arrived=[key for key,path in self.waiting_files.items() if path.is_file()]
            for key in arrived:self.waiting_files.pop(key,None)
            if arrived:self.changed.emit()
        def path(self,ref):
            filename=ref.get('file','')
            if not re.fullmatch('[a-f0-9]{64}\\.(webp|gif|png)',filename):return None
            return folder/'assets'/filename
        def ensure(self,ref):pass
        def close(self):
            super().close()
            for entry in self.images.values():
                movie=entry.get('movie')
                if movie:movie.setFileName('');movie.deleteLater()
            self.images.clear();self.measure_images.clear();self.waiting_files.clear()
    return RecordedMedia(data,settings,online=False,parent=parent)
