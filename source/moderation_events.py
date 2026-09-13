"""Read-only EventSub collector. It never issues moderation or reward fulfillment commands."""
import json,threading,time,urllib.request,urllib.parse,urllib.error
from collections import deque
import websocket
from moderation_evidence import MOD_SCOPES,REWARD_SCOPE,AUTOMOD_SCOPE,millis
from twitch_core import CLIENT_ID

def notification(payload,stamp):
    event=payload['event'];typ=payload['subscription']['type']
    if typ=='automod.message.hold':
        message=event.get('message',{})
        text=message.get('text','') if isinstance(message,dict) else message
        return {'kind':'automod','user':event['user_login'].lower(),'channel':event['broadcaster_user_login'].lower(),
            'at_ms':millis(event.get('held_at',stamp)),'source':'twitch_eventsub','time_basis':'eventsub',
            'message_id':event['message_id'],'message':{'state':'available','id':event['message_id'],
                'user':event['user_login'].lower(),'display_name':event.get('user_name',event['user_login']),
                'user_id':event.get('user_id',''),'channel':event['broadcaster_user_login'].lower(),'text':text,
                'time_utc':event.get('held_at',stamp),'source':'twitch_automod','automod':True},
            'raw':'Задержано AutoMod'}
    if typ.startswith('channel.channel_points_custom_reward_redemption.'):
        return {'kind':'reward','id':event['id'],'channel':event['broadcaster_user_login'].lower(),'buyer':event['user_login'].lower(),
            'reward':event['reward']['id'],'title':event['reward']['title'],'input':event['user_input'],
            'status':event['status'],'at_ms':millis(event['redeemed_at'])}
    if typ!='channel.moderate':return None
    kind=event['action']
    if kind not in ('ban','timeout','unban','untimeout','delete'):return None
    detail=event[kind];user=detail['user_login'].lower();channel=event['broadcaster_user_login'].lower();at=millis(stamp)
    action={'kind':kind,'user':user,'channel':channel,'at_ms':at,'source':'twitch_eventsub','time_basis':'eventsub','context':[]}
    if kind=='timeout':action['duration']=max(0,round((millis(detail['expires_at'])-at)/1000))
    if kind=='delete':
        mid=detail['message_id'];action.update(message_id=mid,message={'state':'available','id':mid,'user':user,'channel':channel,'text':detail['message_body']})
    return {'kind':'executor','actor':event['moderator_user_login'].lower(),'action':action}

class ModerationEvents(threading.Thread):
    def __init__(self,twitch):
        super().__init__(daemon=True,name='Twitch moderation events');self.twitch=twitch;self.stopped=threading.Event();self.connection=None
        self.text='Имена модераторов: расширенный доступ не включён.';self.seen=set();self.order=deque()
    def request(self,token,path,data=None):
        assert path.startswith(('moderation/channels?','eventsub/subscriptions'))
        req=urllib.request.Request('https://api.twitch.tv/helix/'+path,data=None if data is None else json.dumps(data).encode(),
            headers={'Authorization':'Bearer '+token,'Client-Id':CLIENT_ID,'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=8) as r:
            raw=r.read(2*1024*1024+1)
            if len(raw)>2*1024*1024:raise ValueError('Oversized EventSub response')
            return json.loads(raw)
    def subscriptions(self,account):
        token=account['access_token'];uid=account['user_id'];scopes=set(account.get('scopes',[]));wanted=set(self.twitch.wanted_channels());channels={}
        if set(MOD_SCOPES)<=scopes and 'user:read:moderated_channels' in scopes:
            cursor=''
            for _ in range(20):
                if self.stopped.is_set():return []
                result=self.request(token,'moderation/channels?'+urllib.parse.urlencode({'user_id':uid,'first':100,**({'after':cursor} if cursor else {})}))
                channels.update({x['broadcaster_login']:x['broadcaster_id'] for x in result['data'] if x['broadcaster_login'] in wanted})
                cursor=result.get('pagination',{}).get('cursor','')
                if not cursor:break
            if account['login'] in wanted:channels[account['login']]=uid
        subs=[{'type':'channel.moderate','version':'2','condition':{'broadcaster_user_id':bid,'moderator_user_id':uid}} for bid in channels.values()]
        if AUTOMOD_SCOPE in scopes:
            subs += [{'type':'automod.message.hold','version':'1','condition':{'broadcaster_user_id':bid,'moderator_user_id':uid}} for bid in channels.values()]
        if REWARD_SCOPE in scopes and account['login'] in wanted:
            subs += [{'type':'channel.channel_points_custom_reward_redemption.'+suffix,'version':'1','condition':{'broadcaster_user_id':uid}} for suffix in ('add','update')]
        return subs
    def stop(self):
        self.stopped.set()
        if self.connection:
            try:self.connection.abort()
            except Exception:pass
    def consume(self,doc):
        meta=doc['metadata'];key=meta['message_id']
        if key in self.seen:return
        event=notification(doc['payload'],meta['message_timestamp'])
        if event:self.twitch.evidence.append(event)
        self.seen.add(key);self.order.append(key)
        if len(self.order)>10000:self.seen.discard(self.order.popleft())
    def run(self):
        while not self.stopped.is_set():
            account=dict(self.twitch.account or {})
            if not self.twitch.config.get('moderation_details') or not self.twitch.config.get('enabled'):
                self.stopped.wait(.5);continue
            if not account.get('access_token') or not set(MOD_SCOPES+[AUTOMOD_SCOPE])<=set(account.get('scopes',[])):
                self.text='Для имён модераторов и AutoMod нужен новый вход с расширенным доступом.';self.stopped.wait(1);continue
            connection=None
            try:
                subs=self.subscriptions(account)
                if not subs:
                    self.text='На выбранных каналах нет доступных прав модератора/владельца.';self.stopped.wait(60);continue
                connection=websocket.create_connection('wss://eventsub.wss.twitch.tv/ws',timeout=8,redirect_limit=0);self.connection=connection
                first=json.loads(connection.recv());session=first['payload']['session']
                if first['metadata']['message_type']!='session_welcome':raise ValueError('Missing welcome')
                for sub in subs:
                    if self.stopped.is_set():break
                    self.request(account['access_token'],'eventsub/subscriptions',{**sub,'transport':{'method':'websocket','session_id':session['id']}})
                self.text='Подтверждения Twitch подключены · подписок: '+str(len(subs));connection.settimeout(1)
                last=time.monotonic();started=last;wanted=self.twitch.wanted_channels()
                while not self.stopped.is_set():
                    if not self.twitch.config.get('moderation_details') or not self.twitch.config.get('enabled') or (self.twitch.account or {}).get('access_token')!=account['access_token'] or wanted!=self.twitch.wanted_channels():break
                    try:raw=connection.recv()
                    except websocket.WebSocketTimeoutException:
                        if time.monotonic()-last>session.get('keepalive_timeout_seconds',10)+8:raise TimeoutError('EventSub heartbeat')
                        continue
                    if not raw or len(raw)>2*1024*1024:raise ValueError('Invalid EventSub message')
                    last=time.monotonic();doc=json.loads(raw);kind=doc['metadata']['message_type']
                    if kind=='notification':self.consume(doc)
                    elif kind=='session_reconnect':
                        url=doc['payload']['session']['reconnect_url'];p=urllib.parse.urlsplit(url)
                        if p.scheme!='wss' or p.hostname!='eventsub.wss.twitch.tv' or p.path!='/ws' or p.username or p.password or p.port not in (None,443):raise ValueError('Invalid reconnect URL')
                        replacement=websocket.create_connection(url,timeout=8,redirect_limit=0)
                        try:
                            welcome=json.loads(replacement.recv())
                            if welcome['metadata']['message_type']!='session_welcome':raise ValueError('Missing reconnect welcome')
                        except Exception:replacement.close(timeout=1);raise
                        connection.close(timeout=1);connection=replacement;self.connection=connection
                        session=welcome['payload']['session'];connection.settimeout(1);last=time.monotonic()
                    elif kind=='revocation':raise ConnectionError('Recreate subscriptions')
            except Exception:
                self.text='Подтверждения Twitch временно недоступны; возможны пропуски. Переподключаемся.'
                self.stopped.wait(15)
            finally:
                self.connection=None
                if connection:
                    try:connection.close(timeout=1)
                    except Exception:pass
