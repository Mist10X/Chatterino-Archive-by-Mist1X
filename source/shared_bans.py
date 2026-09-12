"""On-demand, read-only Shared Bans client. No reporting or moderation commands."""
import json,queue,re,sqlite3,threading,time,urllib.request,urllib.parse,urllib.error
from contextlib import closing
from pathlib import Path
import websocket
from storage import atomic_write,name
from twitch_core import Credentials,CLIENT_ID

HOST='https://ws.chat.eblo.id'
LIMIT=2_000_000
class SharedError(Exception):pass
class AuthRequired(SharedError):pass
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def request_json(url,body=None,headers=None):
    request=urllib.request.Request(url,None if body is None else json.dumps(body).encode(),{
        'Accept':'application/json','Content-Type':'application/json','User-Agent':'Mist1XArchive/0.14.0',**(headers or {})})
    try:
        with urllib.request.build_opener(NoRedirect).open(request,timeout=8) as response:
            raw=response.read(LIMIT+1)
        if len(raw)>LIMIT:raise SharedError('Ответ сервиса слишком большой.')
        result=json.loads(raw)
        if not isinstance(result,dict):raise ValueError()
        return result
    except urllib.error.HTTPError as exc:
        if exc.code in (401,403):raise AuthRequired('Сервис отклонил доступ. Подключи Chatterino+ заново.') from None
        if exc.code==429:raise SharedError('Сервис ограничил частоту запросов. Повтори позже.') from None
        raise SharedError('Сервис временно недоступен (HTTP '+str(exc.code)+').') from None
    except (OSError,ValueError,urllib.error.URLError):raise SharedError('Не удалось получить ответ сервиса. Сохранённые данные доступны.') from None

def auth_record(value):
    if not isinstance(value,dict) or not isinstance(value.get('ticket'),str) or not value['ticket'] or not str(value.get('uid','')).isdigit():
        raise SharedError('Неожиданный ответ при входе Chatterino+.')
    if not isinstance(value.get('exp'),int):raise SharedError('В ответе отсутствует срок входа.')
    return {k:value[k] for k in ('ticket','exp','renew','renew_exp','login','name','uid','scoped') if k in value}

def parse_card(value,uid,login,now=None):
    if not isinstance(value,dict) or value.get('op')!='sbdoc' or value.get('u')!=uid or not isinstance(value.get('items'),list):
        raise SharedError('Получена карточка другого пользователя или неизвестный формат ответа.')
    if len(value['items'])>5000:raise SharedError('Слишком много записей в ответе.')
    rows=[];seen=set()
    for item in value['items']:
        if not isinstance(item,dict):raise SharedError('Неизвестный формат записи бана.')
        room=str(item.get('room',''));channel=item.get('login','');since=item.get('since')
        if not room.isdigit() or not isinstance(channel,str) or type(since) is not int or not 0<since<253402214400:
            raise SharedError('В записи бана нет канала или времени.')
        try:channel=name(channel)
        except ValueError:raise SharedError('Некорректное имя канала в ответе.') from None
        if not channel:raise SharedError('В записи отсутствует канал.')
        key=(room,since)
        if key in seen:continue
        seen.add(key);messages=item.get('msgs',[])
        if not isinstance(messages,list) or len(messages)>1000:raise SharedError('Неизвестный формат сообщений.')
        clean=[]
        for msg in messages:
            if not isinstance(msg,dict) or not isinstance(msg.get('t'),str) or type(msg.get('ts')) is not int or not 0<msg['ts']<253402214400:
                raise SharedError('Неизвестный формат сообщения сервиса.')
            if len(msg['t'])>20000:raise SharedError('Сообщение слишком длинное.')
            clean.append({'text':msg['t'],'at':msg['ts'],'automod':msg.get('am') in (1,True)})
        rows.append({'channel':channel,'channel_id':room,'since':since,'messages':sorted(clean,key=lambda m:m['at']),
            'user':login,'user_id':uid})
    note=value.get('note','')
    if not isinstance(note,str):raise SharedError('Неизвестный формат примечания.')
    return {'user':login,'user_id':uid,'fetched_at':int(now or time.time()),'items':sorted(rows,key=lambda x:x['since'],reverse=True),'note':note[:4000]}

class API:
    def begin(self):
        value=request_json(HOST+'/auth/begin');url=value.get('url','');p=urllib.parse.urlsplit(url)
        if p.scheme!='https' or p.hostname!='id.twitch.tv' or p.path!='/oauth2/authorize' or p.username or p.password or p.port not in (None,443):
            raise SharedError('Сервис вернул неизвестную страницу входа.')
        if not isinstance(value.get('state'),str) or not value['state']:raise SharedError('Сервис не выдал запрос входа.')
        return value
    def poll(self,state):return request_json(HOST+'/auth/poll?'+urllib.parse.urlencode({'state':state}))
    def renew(self,account):
        if not account.get('renew') or account.get('renew_exp',0)<=time.time():raise AuthRequired('Срок входа истёк. Подключи Chatterino+ заново.')
        return auth_record(request_json(HOST+'/auth/renew',{'renew':account['renew']}))
    def user_id(self,login,token):
        if not token:raise SharedError('ID пользователя не найден локально. Подключи Twitch в архиве для поиска по нику.')
        try:value=request_json('https://api.twitch.tv/helix/users?'+urllib.parse.urlencode({'login':login}),headers={'Client-Id':CLIENT_ID,'Authorization':'Bearer '+token})
        except AuthRequired:raise SharedError('Twitch не подтвердил поиск по нику. Подключи Twitch заново в настройках архива.') from None
        for user in value.get('data',[]):
            if user.get('login','').lower()==login and str(user.get('id','')).isdigit():return str(user['id'])
        raise SharedError('Пользователь Twitch не найден.')
    def card(self,account,uid,login):
        connection=None
        try:
            connection=websocket.create_connection('wss://ws.chat.eblo.id/',timeout=8,redirect_limit=0,
                header={'User-Agent':'Mist1XArchive/0.14.0'})
            connection.send(json.dumps({'op':'hello','v':2,'ticket':account['ticket']}))
            requested=False;deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                raw=connection.recv()
                if not raw:raise SharedError('Сервис закрыл соединение. Повтори позже.')
                if len(raw)>LIMIT:raise SharedError('Ответ слишком большой.')
                value=json.loads(raw)
                if not isinstance(value,dict):raise SharedError('Неизвестный ответ сервиса.')
                op=value.get('op')
                if op=='hello' and not requested:
                    if value.get('verified') is not True:raise AuthRequired('Сервис не подтвердил вход. Подключи Chatterino+ заново.')
                    connection.send(json.dumps({'op':'sbget','u':uid}));requested=True
                elif op=='sbdoc' and requested:return parse_card(value,uid,login)
                elif op in ('error','err'):raise SharedError('Сервис отклонил запрос общих банов.')
            raise SharedError('Сервис не ответил вовремя.')
        except (websocket.WebSocketException,OSError,ValueError):raise SharedError('Нет связи с общими банами. Сохранённая карточка доступна.') from None
        finally:
            if connection:
                try:connection.close(timeout=1)
                except Exception:pass

class Store:
    def __init__(self,data):self.folder=Path(data)/'shared-bans'
    def path(self,user):return self.folder/'cards'/(name(user)+'.json')
    def load(self,user,path=None):
        try:
            p=path or self.path(user)
            if p.stat().st_size>LIMIT:return None
            value=json.loads(p.read_text(encoding='utf-8'))
            if not isinstance(value,dict) or value.get('user')!=name(user) or not isinstance(value.get('items'),list):return None
            uid=value['user_id'];at=value['fetched_at']
            if not isinstance(uid,str) or not uid.isdigit() or type(at) is not int or not 0<at<253402214400:return None
            raw={'op':'sbdoc','u':uid,'note':value.get('note',''),'items':[
                {'room':r['channel_id'],'login':r['channel'],'since':r['since'],'msgs':[
                    {'t':m['text'],'ts':m['at'],'am':m['automod']} for m in r['messages']]} for r in value['items']]}
            return parse_card(raw,uid,name(user),at)
        except (OSError,ValueError,KeyError,TypeError,SharedError):return None
    def preserve(self,value):
        for row in value['items']:
            target=self.folder/'history'/name(value['user'])/(row['channel_id']+'-'+str(row['since'])+'.json')
            previous=self.load(value['user'],path=target)
            if previous and previous['items'] and previous['user_id']==value['user_id']:
                messages={(m['at'],m['text']):dict(m) for m in previous['items'][0]['messages']}
                for m in row['messages']:
                    key=(m['at'],m['text']);messages[key]={**m,'automod':m['automod'] or messages.get(key,{}).get('automod',False)}
                row={**row,'messages':sorted(messages.values(),key=lambda m:m['at'])}
            atomic_write(target,json.dumps({**value,'fetched_at':max(value['fetched_at'],previous['fetched_at'] if previous else 0),'items':[row]},ensure_ascii=False))
    def save(self,value):
        self.preserve(value)
        atomic_write(self.path(value['user']),json.dumps(value,ensure_ascii=False))

class SharedService(threading.Thread):
    def __init__(self,data,token=lambda:None,network=True,api=None,credentials=None,connection_state=lambda:{}):
        super().__init__(daemon=True);self.data=Path(data);self.store=Store(data);self.token=token;self.network=network
        self.api=api or API();self.credentials=credentials or Credentials(self.store.folder)
        self.commands=queue.Queue(maxsize=4);self.results=queue.Queue();self.stopped=threading.Event()
        self.account=None;self.auth=None;self.next_poll=0;self.last_request=0;self.pending=False;self.lock=threading.Lock();self.known={};self.connection_state=connection_state;self.recovery=None
    def command(self,kind,value=None):
        with self.lock:
            if self.pending:return False
            self.pending=True
        try:self.commands.put_nowait((kind,value));return True
        except queue.Full:
            with self.lock:self.pending=False
            return False
    def result(self,kind,value):self.results.put((kind,value))
    def resolve(self,user):
        if user in self.known:return self.known[user]
        cached=self.store.load(user)
        if cached and str(cached.get('user_id','')).isdigit():uid=cached['user_id']
        else:
            uid=None;p=self.data/'panel-cache/moderation.sqlite3'
            if p.is_file():
                try:
                    with closing(sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True,timeout=1)) as db:
                        row=db.execute('SELECT user_id FROM identities WHERE user=?',(user,)).fetchone()
                    if row and str(row[0]).isdigit():uid=str(row[0])
                except sqlite3.Error:pass
            if not uid:uid=self.api.user_id(user,self.token())
        self.known[user]=uid;return uid
    def fetch(self,user,force=False):
        user=name(user);cached=self.store.load(user)
        if cached:self.result('card',cached)
        if not self.network:
            if not cached:raise SharedError('В этом режиме доступен только сохранённый архив.')
            return
        if cached and not force and time.time()-cached.get('fetched_at',0)<60:return
        if time.monotonic()-self.last_request<3:raise SharedError('Подожди несколько секунд перед новым запросом.')
        self.last_request=time.monotonic()
        if not self.account:raise AuthRequired('Сначала подключи сервис Chatterino+.')
        if self.account.get('exp',0)<=time.time()+30:
            updated=self.api.renew(self.account);self.credentials.save(updated);self.account=updated
        uid=self.resolve(user);value=self.api.card(self.account,uid,user);self.store.save(value);self.result('card',value)
    def run(self):
        try:
            saved=self.credentials.load();self.account=auth_record(saved) if saved else None
        except (OSError,SharedError):self.result('error','Не удалось открыть сохранённый вход. Подключи Chatterino+ заново.')
        if self.network:
            from ban_recovery import Recovery
            try:self.recovery=Recovery(self)
            except (OSError,sqlite3.Error):self.result('recovery_status','Не удалось открыть очередь восстановления контекста.')
        self.result('account',self.account.get('login','') if self.account else '')
        while not self.stopped.is_set():
            try:
                try:kind,value=self.commands.get(timeout=.2)
                except queue.Empty:kind=None
                if kind:
                    try:
                        if kind=='fetch':self.fetch(*value)
                        elif kind=='recover' and self.recovery:self.recovery.prioritize(value)
                        elif kind=='auth':
                            if not self.network:raise SharedError('Вход недоступен в тестовом режиме.')
                            self.auth=self.api.begin();self.auth['deadline']=time.monotonic()+600;self.next_poll=time.monotonic()+5
                            self.result('auth_url',self.auth['url'])
                        elif kind=='cancel':self.auth=None;self.result('status','Ожидание входа отменено.')
                        elif kind=='disconnect':
                            self.auth=None;self.account=None;self.credentials.clear();self.result('account','')
                    finally:
                        with self.lock:self.pending=False
                if self.recovery and not kind:self.recovery.step()
                if self.auth and time.monotonic()>=self.next_poll:
                    self.next_poll=time.monotonic()+5
                    if time.monotonic()>self.auth['deadline']:
                        self.auth=None;raise AuthRequired('Время входа истекло. Нажми «Подключить» снова.')
                    value=self.api.poll(self.auth['state'])
                    if value.get('ticket'):
                        account=auth_record(value);self.credentials.save(account);self.account=account;self.auth=None
                        self.result('account',self.account.get('login',''))
                    elif value.get('denied') or value.get('expired'):
                        self.auth=None;raise AuthRequired('Вход не подтверждён. Попробуй снова.')
            except AuthRequired as exc:self.auth=None;self.result('auth_required',str(exc))
            except SharedError as exc:self.result('error',str(exc))
            except Exception:self.result('error','Не удалось получить общие баны. Локальная история сохранена.')
        if self.recovery:self.recovery.close()
        self.auth=None;self.account=None
