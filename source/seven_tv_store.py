"""Persistent 7TV catalog snapshots and per-message bindings, separate from JSONL."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import urllib.request
import urllib.error
from urllib.parse import urlsplit
from storage import atomic_write, name
from branding import EXE_STEM,VERSION

API='https://7tv.io/v3'
ID=re.compile(r'(?:[0-9a-f]{24}|[0-9A-HJKMNP-TV-Z]{26})\Z',re.I)
MAX_JSON=8*1024*1024
MAX_PERSONAL_JSON=16*1024*1024
MAX_IMAGE=16*1024*1024

def permitted_url(url):
    try:
        p=urlsplit(url);port=p.port
    except (ValueError,TypeError):return False
    if p.scheme!='https' or p.username or p.password or port not in (None,443) or p.query or p.fragment:
        return False
    if p.hostname=='7tv.io':
        return bool(re.fullmatch(r'/v3/(gql|emote-sets/global|users/twitch/[0-9]{1,25})',p.path))
    if p.hostname=='cdn.7tv.app':
        return bool(re.fullmatch(r'/emote/[a-zA-Z0-9_-]{1,80}/[1-4]x(?:_static)?\.(webp|gif|png)',p.path))
    return False

class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        if not permitted_url(newurl):raise ValueError('7TV returned an unexpected redirect')
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def fetch(url,maximum):
    if not permitted_url(url):raise ValueError('Unexpected 7TV URL')
    opener=urllib.request.build_opener(SafeRedirect())
    request=urllib.request.Request(url,headers={'User-Agent':EXE_STEM+'/'+VERSION,'Accept':'application/json' if url.startswith(API) else 'image/webp,image/gif,image/png'})
    with opener.open(request,timeout=10) as response:
        data=response.read(maximum+1)
        if len(data)>maximum:raise ValueError('7TV response exceeds its size limit')
        if not data:raise ValueError('Empty 7TV response')
        return data

def _parse_emotes(source,personal=False,uid=''):
    if not isinstance(source,dict) or not isinstance(source.get('emotes'),list):raise ValueError('Invalid emote set')
    if len(source['emotes'])>10000:raise ValueError('Emote set is too large')
    result={}
    for active in source['emotes']:
        if not isinstance(active,dict):continue
        alias=active.get('name');data=active.get('data')
        if not isinstance(alias,str) or not alias or len(alias)>100 or re.search(r'\s',alias) or not isinstance(data,dict):continue
        if personal and 'PERSONAL' not in (data.get('state') or []):continue
        eid=str(data.get('id',''))
        if not ID.fullmatch(eid):continue
        host=data.get('host') or {};base=host.get('url','')
        if not isinstance(base,str):continue
        if base.startswith('//'):base='https:'+base
        candidates=[]
        for image in host.get('files',[]):
            if not isinstance(image,dict) or image.get('format') not in ('WEBP','GIF','PNG'):continue
            filename=image.get('name','');url=base.rstrip('/')+'/'+filename
            try:w,h,size=int(image.get('width',0)),int(image.get('height',0)),int(image.get('size',0))
            except (ValueError,TypeError):continue
            if not (1<=w<=2048 and 1<=h<=512 and w*h<=1024*512) or size>MAX_IMAGE or not permitted_url(url):continue
            candidates.append((image.get('format')!='WEBP',abs(h-96),-w,filename,url,w,h))
        if not candidates:continue
        _,_,_,filename,url,w,h=min(candidates)
        key=hashlib.sha256(url.encode()).hexdigest()
        ref={'name':alias,'id':eid,'url':url,'key':key,'file':key+Path(filename).suffix,
            'width':w,'height':h,'animated':bool(data.get('animated')),
            'zero_width':bool(int(active.get('flags',0))&1 or int(data.get('flags',0))&256)}
        if personal:ref.update(personal=True,owner_twitch_id=uid,set_id=str(source.get('id','')))
        result[alias]=ref
    return result

def parse_catalog(payload,channel,uid='',now=None):
    """Keep aliases, modern ULIDs, and per-set overlay flags from the v3 response."""
    if not isinstance(payload,dict):raise ValueError('Invalid 7TV catalog')
    source=payload if channel=='global' else payload.get('emote_set')
    if source is None and channel!='global':source={'id':'','emotes':[]}
    result=_parse_emotes(source)
    return {'version':1,'channel':channel,'twitch_id':uid,'set_id':str(source.get('id','')),
        'observed_at':time.time() if now is None else now,'emotes':result}

def parse_personal_user(user,uid,now=None):
    """Merge all entitled sets; PERSONAL entries override earlier personal aliases."""
    if not re.fullmatch(r'[0-9]{1,25}',str(uid)):raise ValueError('Invalid Twitch user ID')
    if user is None:user={}
    if not isinstance(user,dict):raise ValueError('Invalid 7TV user')
    sets=user.get('emote_sets') or []
    if not isinstance(sets,list) or len(sets)>100:raise ValueError('Invalid personal emote sets')
    emotes={};set_ids=[]
    for source in sets:
        if not isinstance(source,dict):continue
        sid=str(source.get('id',''))
        if not ID.fullmatch(sid):continue
        parsed=_parse_emotes(source,personal=True,uid=str(uid))
        if parsed:set_ids.append(sid);emotes.update(parsed)
    return {'version':1,'twitch_id':str(uid),'set_ids':set_ids,
        'observed_at':time.time() if now is None else now,'emotes':emotes}

def fetch_personal_users(uids,fetcher=None):
    """One bounded GraphQL request resolves up to 20 Twitch users."""
    uids=list(dict.fromkeys(str(uid) for uid in uids))
    if not uids or len(uids)>20 or any(not re.fullmatch(r'[0-9]{1,25}',uid) for uid in uids):raise ValueError('Invalid personal batch')
    declarations=','.join(f'$u{i}:String!' for i in range(len(uids)))
    fields='id name emotes{id name flags data{id name state flags animated host{url files{name format width height size}}}}'
    selections=' '.join(f'u{i}:userByConnection(id:$u{i},platform:TWITCH){{id emote_sets(entitled:true){{{fields}}}}}' for i in range(len(uids)))
    body=json.dumps({'query':f'query({declarations}){{{selections}}}','variables':{f'u{i}':uid for i,uid in enumerate(uids)}}).encode()
    url=API+'/gql'
    if fetcher is not None:payload=fetcher(url,body,MAX_PERSONAL_JSON)
    else:
        opener=urllib.request.build_opener(SafeRedirect())
        request=urllib.request.Request(url,body,headers={'User-Agent':EXE_STEM+'/'+VERSION,'Accept':'application/json','Content-Type':'application/json'})
        with opener.open(request,timeout=12) as response:
            payload=response.read(MAX_PERSONAL_JSON+1)
            if len(payload)>MAX_PERSONAL_JSON:raise ValueError('7TV personal response exceeds its size limit')
    result=json.loads(payload)
    if not isinstance(result,dict) or not isinstance(result.get('data'),dict):raise ValueError('Invalid 7TV personal response')
    return {uid:parse_personal_user(result['data'].get(f'u{i}'),uid) for i,uid in enumerate(uids)}

class EmoteStore:
    def __init__(self,data):
        self.root=Path(data)/'7tv';self.catalog_dir=self.root/'catalogs';self.personal_dir=self.root/'personal';self.assets=self.root/'assets'
        self.catalog_dir.mkdir(parents=True,exist_ok=True);self.personal_dir.mkdir(exist_ok=True);self.assets.mkdir(exist_ok=True)
        self.catalogs={};self.personal={};self.set_users={};self.ids={}
        for path in self.catalog_dir.glob('*.json'):
            try:
                catalog=json.loads(path.read_text(encoding='utf-8'))
                channel=path.stem
                if channel!='global':name(channel)
                if catalog.get('channel')==channel and isinstance(catalog.get('emotes'),dict):
                    self.catalogs[channel]=catalog
                    if str(catalog.get('twitch_id','')).isdigit():self.ids[channel]=catalog['twitch_id']
            except (OSError,ValueError):pass
        for path in self.personal_dir.glob('*.json'):
            try:
                uid=path.stem
                if not re.fullmatch(r'[0-9]{1,25}',uid):continue
                catalog=json.loads(path.read_text(encoding='utf-8'))
                if catalog.get('twitch_id')==uid and isinstance(catalog.get('emotes'),dict):self._remember_personal(catalog)
            except (OSError,ValueError):pass
        self.db=sqlite3.connect(self.root/'message-bindings.sqlite3',timeout=5)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS bindings (key TEXT PRIMARY KEY,channel TEXT,observed_at REAL,refs TEXT)')
        self.memo={};self.dirty=False
    def install(self,catalog):
        channel=catalog['channel']
        if channel!='global':name(channel)
        atomic_write(self.catalog_dir/(channel+'.json'),json.dumps(catalog,ensure_ascii=False,separators=(',',':')))
        self.catalogs[channel]=catalog
        if catalog.get('twitch_id'):self.ids[channel]=catalog['twitch_id']
        # Frozen rows remain in SQLite. Only unresolved in-memory rows are retried.
        self.memo={k:v for k,v in self.memo.items() if v[1]}
    def _remember_personal(self,catalog):
        uid=catalog['twitch_id'];old=self.personal.get(uid,{})
        for sid in old.get('set_ids',[]):self.set_users.get(sid,set()).discard(uid)
        self.personal[uid]=catalog
        for sid in catalog.get('set_ids',[]):self.set_users.setdefault(sid,set()).add(uid)
    def install_personal(self,catalog):
        uid=catalog['twitch_id']
        if not re.fullmatch(r'[0-9]{1,25}',uid):raise ValueError('Invalid Twitch user ID')
        atomic_write(self.personal_dir/(uid+'.json'),json.dumps(catalog,ensure_ascii=False,separators=(',',':')))
        self._remember_personal(catalog)
        # Saved bindings are historical snapshots. Only unresolved rows retry.
        self.memo={k:v for k,v in self.memo.items() if v[1]}
    def users_for_set(self,set_id):return set(self.set_users.get(set_id,set()))
    def current(self,channel,user_id=''):
        result=dict(self.catalogs.get('global',{}).get('emotes',{}))
        result.update(self.catalogs.get(channel,{}).get('emotes',{}))
        if user_id in self.personal:result.update(self.personal[user_id].get('emotes',{}))
        personal=self.personal.get(user_id)
        complete=channel in self.catalogs and 'global' in self.catalogs and (not user_id or
            (personal is not None and time.time()-personal.get('observed_at',0)<900))
        return result,complete
    @staticmethod
    def message_key(row,slot='body'):
        text=row.get('text','')
        return hashlib.sha256(json.dumps(['personal-v1',row.get('channel',''),row.get('user',''),row.get('id',''),row.get('time_utc',''),slot,text],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    def bind(self,row,slot='body'):
        channel=row.get('channel','');key=self.message_key(row,slot)
        if key in self.memo:return self.memo[key][0]
        saved=self.db.execute('SELECT refs FROM bindings WHERE key=?',(key,)).fetchone()
        if saved:
            try:refs=json.loads(saved[0]);self.memo[key]=(refs,True);return refs
            except ValueError:pass
        user_id=str(row.get('user_id','')) if str(row.get('user_id','')).isdigit() else ''
        catalog,complete=self.current(channel,user_id)
        # Until a user's personal catalog is known, showing raw aliases avoids a
        # same-name channel emote briefly impersonating their personal choice.
        refs={} if user_id and user_id not in self.personal else {
            token:catalog[token] for token in re.findall(r'\S+',row.get('text','')) if token in catalog}
        if complete:
            self.db.execute('INSERT OR IGNORE INTO bindings VALUES(?,?,?,?)',(key,channel,time.time(),json.dumps(refs,ensure_ascii=False,separators=(',',':'))));self.dirty=True
        if len(self.memo)>8000:self.memo.clear()
        self.memo[key]=(refs,complete);return refs
    def flush(self):
        if self.dirty:self.db.commit();self.dirty=False
    def close(self):self.flush();self.db.close()

def groups(text,refs):
    """Preserve whitespace; adjacent overlay emotes share their base emote's box."""
    result=[]
    for token in re.findall(r'\s+|\S+',text):
        ref=refs.get(token)
        if ref:
            if ref.get('zero_width') and len(result)>=2 and result[-1].get('text','').isspace() and '\n' not in result[-1]['text'] and 'emotes' in result[-2]:
                gap=result.pop()['text'];result[-1]['emotes'].append(ref);result[-1]['text']+=gap+token
            else:result.append({'text':token,'emotes':[ref]})
        else:
            if result and 'emotes' not in result[-1] and not (token.isspace() or result[-1]['text'].isspace()):result[-1]['text']+=token
            else:result.append({'text':token})
    return result
