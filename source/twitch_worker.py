"""Single background owner of OAuth state, IRC socket and direct archive writer."""
import json
from pathlib import Path
import queue
import socket
import ssl
import threading
import time
from collections import deque
from twitch_core import API, CLIENT_ID, SCOPES, Credentials, MessageWriter, TwitchError, parse_irc, chat_record
from storage import atomic_write
from deletions import DeletionCapture


class TwitchWorker(threading.Thread):
    def __init__(self, data, config, api=None, credentials=None, clock=time.monotonic, wall_clock=time.time):
        super().__init__(daemon=True)
        self.folder = Path(data) / 'twitch';self.config = dict(config)
        self.api = api or API();self.credentials = credentials or Credentials(self.folder);self.clock = clock;self.wall_clock=wall_clock
        self.commands = queue.Queue();self.stopped = threading.Event();self.lock = threading.Lock()
        self.state = {'phase': 'off', 'text': 'Twitch не подключён', 'login': '', 'joined': [], 'saved': 0, 'deleted': 0,
            'auth_url': '', 'auth_code': '', 'auth_retries':0, 'auth_reason':''}
        self.replay = None;self.identity_seen={}
        from moderation_evidence import EvidenceWriter
        self.evidence=EvidenceWriter(data)
        self.users = {};self.account = None;self.auth = None;self.connection = None
        self.joined = set();self.pending = {};self.failed = set();self.buffer = b''
        self.join_retries={};self.join_attempts={};self.channel_errors={}
        self.connection_events=deque(maxlen=100);self.events_dirty=False
        self.next_join = 0.;self.last_traffic = 0.;self.next_retry = 0.;self.next_validate = 0.;self.token_deadline = 0.
        self.auth_failures=0;self.refresh_rejections=0;self.auth_blocked=False;self.last_loop_wall=self.wall_clock()
        self.ready = False;self.writer = None;self.deletions = DeletionCapture(self.folder);self.last_status_write = -100.

    def wanted_channels(self):
        return sorted(set(self.config["channels"]) | set(self.replay.wanted() if self.replay else []))

    def snapshot(self):
        with self.lock:return {**self.state, 'joined': list(self.state['joined'])}

    def report(self, **values):
        with self.lock:self.state.update(values)

    def command(self, kind, value=None):self.commands.put((kind, value))

    def persist_status(self, force=False):
        if not force and self.clock() - self.last_status_write < 2:return
        state = self.snapshot()
        public = {key: state[key] for key in ('phase', 'text', 'login', 'joined', 'saved', 'deleted','auth_retries','auth_reason')}
        public['updated_at'] = time.time()
        public['channel_errors']=dict(self.channel_errors)
        try:
            atomic_write(self.folder / 'status.json', json.dumps(public, ensure_ascii=False))
            if self.events_dirty:
                atomic_write(self.folder/'connection-events.json',json.dumps(list(self.connection_events),ensure_ascii=False))
                self.events_dirty=False
        except OSError:return
        self.last_status_write = self.clock()

    def close_socket(self):
        if self.connection:
            try:self.connection.close()
            except OSError:pass
        self.connection = None;self.ready = False;self.joined.clear();self.pending.clear();self.failed.clear();self.buffer = b''
        self.join_retries.clear();self.join_attempts.clear();self.channel_errors.clear()
        self.report(joined=[])
        if self.replay:self.replay.connection([])

    def validate(self):
        result = self.api.request('validate', token=self.account['access_token'])
        if result.get('client_id') != CLIENT_ID or not set(SCOPES) <= set(result.get('scopes', [])):
            raise TwitchError('rights', 'Вход Twitch не содержит нужного разрешения. Подключи аккаунт заново.')
        if not result.get('login') or not result.get('user_id'):raise TwitchError('rights')
        self.account.update(login=result['login'], user_id=result['user_id'],scopes=result.get('scopes',[]))
        self.auth_failures=0;self.refresh_rejections=0;self.auth_blocked=False
        self.next_validate = self.clock() + 3600
        self.token_deadline = self.clock() + max(0, int(result.get('expires_in', 0)))
        self.report(login=result['login'])

    def refresh(self):
        self.close_socket()
        response = self.api.request('token', {'grant_type': 'refresh_token', 'refresh_token': self.account['refresh_token'], 'client_id': CLIENT_ID})
        if not response.get('access_token') or not response.get('refresh_token'):
            raise TwitchError('refresh_response','Twitch вернул неполный ответ. Повторим обновление входа.',source='refresh')
        self.account.update(access_token=response['access_token'], refresh_token=response['refresh_token'])
        # Persist rotating refresh token immediately, before further network calls.
        self.credentials.save(self.account);self.validate();self.credentials.save(self.account)

    def ensure_token(self):
        if not self.next_validate:
            try:self.validate()
            except TwitchError as exc:
                if exc.code != 401:raise
                self.refresh()
        if self.clock() >= self.token_deadline - 180:self.refresh()
        elif self.clock() >= self.next_validate:
            try:self.validate()
            except TwitchError as exc:
                if exc.code != 401:raise
                self.refresh()

    def start_auth(self):
        self.close_socket();self.auth = None;self.auth_blocked=False;self.report(phase='auth', text='Открываем вход Twitch…', auth_url='', auth_code='')
        from moderation_evidence import MOD_SCOPES,REWARD_SCOPE,AUTOMOD_SCOPE
        scopes=SCOPES+(MOD_SCOPES+[REWARD_SCOPE,AUTOMOD_SCOPE,'user:read:moderated_channels'] if self.config.get('moderation_details') else [])
        result = self.api.request('device', {'client_id': CLIENT_ID, 'scopes': ' '.join(scopes)})
        from urllib.parse import urlparse
        url = result.get('verification_uri', '');parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.hostname != 'www.twitch.tv' or parsed.path != '/activate':
            raise TwitchError('auth', 'Twitch вернул неизвестный адрес входа.')
        self.auth = {'device_code': result['device_code'], 'until': self.clock() + min(1800, int(result['expires_in'])),
                     'interval': max(5, int(result.get('interval', 5))), 'next': self.clock() + max(5, int(result.get('interval', 5)))}
        self.report(phase='auth', text='Подтверди вход на официальной странице Twitch', auth_url=url, auth_code=result['user_code'])

    def poll_auth(self):
        if self.clock() >= self.auth['until']:
            self.auth = None;self.report(phase='off', text='Время входа истекло. Нажми «Подключить Twitch» снова.', auth_url='', auth_code='');return
        if self.clock() < self.auth['next']:return
        self.auth['next'] = self.clock() + self.auth['interval']
        try:
            result = self.api.request('token', {'client_id': CLIENT_ID, 'device_code': self.auth['device_code'],
                'grant_type': 'urn:ietf:params:oauth:grant-type:device_code'})
        except TwitchError as exc:
            if exc.code == 'authorization_pending':return
            if exc.code == 'slow_down':self.auth['interval'] += 5;self.auth['next'] = self.clock() + self.auth['interval'];return
            if exc.code == 'network':self.report(text='Нет связи с Twitch; ожидание входа продолжается.');return
            self.auth = None;self.report(auth_url='', auth_code='');raise
        self.auth = None;self.report(auth_url='', auth_code='')
        if not result.get('access_token') or not result.get('refresh_token'):raise TwitchError('rights')
        self.account = {'access_token': result['access_token'], 'refresh_token': result['refresh_token']}
        self.credentials.save(self.account);self.validate();self.credentials.save(self.account)
        self.next_retry = 0;self.auth_failures=0;self.refresh_rejections=0;self.auth_blocked=False
        self.report(phase='paused', text='Вход выполнен. Включи запись и выбери каналы.',auth_retries=0,auth_reason='')

    def disconnect(self):
        self.close_socket();self.auth = None;old = self.account;self.account = None;self.auth_failures=0;self.refresh_rejections=0;self.auth_blocked=False
        self.credentials.clear();self.report(phase='off', text='Аккаунт отключён. Сохранённые сообщения остаются.', login='', auth_url='', auth_code='',auth_retries=0,auth_reason='')
        if old:
            try:self.api.request('revoke', {'client_id': CLIENT_ID, 'token': old['access_token']})
            except TwitchError:self.report(text='Вход удалён с компьютера. Отозвать разрешение также можно в настройках подключений Twitch.')

    def retry_now(self):
        self.close_socket();self.next_retry=0
        if not self.account:
            self.auth_blocked=True
            self.report(phase='auth_required',text='Сохранённого входа нет. Подключи Twitch заново.')
            return
        self.auth_blocked=False;self.auth_failures=0;self.refresh_rejections=0;self.next_validate=0;self.token_deadline=0
        self.connection_event('manual_retry')
        self.report(phase='reconnecting',text='Повторяем подключение Twitch сейчас…',auth_retries=0,auth_reason='')

    def wake_check(self):
        now=self.wall_clock();gap=now-self.last_loop_wall;self.last_loop_wall=now
        if gap<20:return False
        self.connection_event('wake_detected',reason=str(int(gap)))
        if self.account and not self.auth_blocked:
            self.close_socket();self.auth_failures=0;self.refresh_rejections=0;self.next_retry=0;self.next_validate=0
            self.report(phase='reconnecting',text='Компьютер снова активен; подключаем Twitch без ожидания…',auth_retries=0,auth_reason='')
        return True

    def process_commands(self):
        while True:
            try:kind, value = self.commands.get_nowait()
            except queue.Empty:return
            if kind == 'users':self.users = dict(value)
            elif kind == 'config':
                previous = self.config;self.config = dict(value);self.next_retry = 0
                if previous.get('channels') != self.config.get('channels'):
                    self.failed.clear();self.join_retries.clear();self.join_attempts.clear();self.channel_errors.clear()
                self.deletions.retain_channels(self.config['channels'] if self.config['enabled'] else [])
                if not self.config['enabled']:self.close_socket()
            elif kind == 'replay_changed':self.failed.clear();self.join_retries.clear();self.join_attempts.clear();self.channel_errors.clear();self.next_retry=0
            elif kind == 'auth':self.start_auth()
            elif kind == 'cancel_auth':
                self.auth = None;self.report(auth_url='', auth_code='', phase='off', text='Вход отменён')
            elif kind == 'disconnect':self.disconnect()
            elif kind == 'retry':self.retry_now()

    def send(self, line):self.connection.sendall((line + '\r\n').encode())

    def connection_event(self,kind,channel='',reason=''):
        # Deliberately exclude raw IRC, message bodies and OAuth credentials.
        self.connection_events.append({'at':time.time(),'event':kind,'channel':channel,'reason':reason[:80]})
        self.events_dirty=True

    def auth_failure(self,exc):
        self.close_socket();code=str(exc.code)
        if exc.code=='client_type':
            self.auth_blocked=True;self.connection_event('auth_required',reason='client_type_confidential')
            self.report(phase='auth_required',text='Twitch требует client secret. В консоли разработчика установи для приложения тип Public, затем нажми «Повторить сейчас».',
                auth_retries=self.auth_failures,auth_reason='client_type_confidential')
            self.next_retry=0;return
        if exc.code in ('invalid_refresh','rights'):
            self.auth_blocked=True;self.account=None;self.credentials.clear()
            reason='refresh_token_invalid' if exc.code=='invalid_refresh' else 'permissions_invalid'
            self.connection_event('auth_required',reason=reason)
            message=('Twitch подтвердил, что сохранённый вход недействителен. Подключи Twitch заново.'
                if exc.code=='invalid_refresh' else 'Разрешение Twitch больше не подходит. Подключи Twitch заново.')
            self.report(phase='auth_required',text=message,login='',auth_retries=self.auth_failures,auth_reason=reason)
            self.next_retry=0;return
        if not self.account:
            self.auth_blocked=True;self.connection_event('auth_required',reason=code)
            self.report(phase='auth_required',text='Вход не завершён. Нажми «Подключить Twitch» и попробуй снова.',auth_reason=code)
            return
        self.auth_failures+=1
        self.refresh_rejections=self.refresh_rejections+1 if exc.code=='refresh_rejected' else 0
        if self.refresh_rejections>=3:
            self.auth_blocked=True;self.connection_event('auth_required',reason='refresh_rejected_repeated')
            self.report(phase='auth_required',text='Twitch несколько раз отклонил обновление входа. Нажми «Повторить сейчас» или подключи аккаунт заново.',
                auth_retries=self.auth_failures,auth_reason='refresh_rejected_repeated')
            self.next_retry=0;return
        delay=(5,10,20,30,60)[min(self.auth_failures-1,4)]
        if exc.code==401:self.next_validate=0;self.token_deadline=0
        self.connection_event('auth_retry',reason=code)
        self.report(phase='reconnecting',text=f'Вход Twitch временно не подтверждён. Попытка {self.auth_failures}; повтор через {delay} с.',
            auth_retries=self.auth_failures,auth_reason=code)
        self.next_retry=self.clock()+delay

    def confirm_channel(self,channel):
        if not self.config['enabled'] or channel not in self.wanted_channels():return
        changed=channel not in self.joined
        self.joined.add(channel);self.pending.pop(channel,None);self.failed.discard(channel)
        self.join_retries.pop(channel,None);self.join_attempts.pop(channel,None);self.channel_errors.pop(channel,None)
        if changed:
            self.connection_event('connected',channel);self.report(joined=sorted(self.joined))
            if self.replay:self.replay.connection(self.joined)

    def retry_channel(self,channel,reason,unavailable=False):
        if channel not in self.wanted_channels():return
        was_joined=channel in self.joined
        self.joined.discard(channel);self.pending.pop(channel,None);self.failed.add(channel)
        attempt=min(5,self.join_attempts.get(channel,0)+1);self.join_attempts[channel]=attempt
        delay=300 if unavailable else min(300,30*2**(attempt-1))
        self.join_retries[channel]=self.clock()+delay;self.channel_errors[channel]=reason
        self.connection_event('retry_scheduled',channel,reason)
        self.report(joined=sorted(self.joined))
        if was_joined and self.replay:self.replay.connection(self.joined)

    def connect(self):
        self.report(phase='connecting', text='Соединяемся с чатом Twitch…')
        raw = socket.create_connection(('irc.chat.twitch.tv', 6697), timeout=8)
        try:self.connection = ssl.create_default_context().wrap_socket(raw, server_hostname='irc.chat.twitch.tv')
        except Exception:raw.close();raise
        self.connection.settimeout(.4);self.last_traffic = self.clock();self.next_join = self.clock()
        self.send('CAP REQ :twitch.tv/tags twitch.tv/commands')
        self.send('PASS oauth:' + self.account['access_token']);self.send('NICK ' + self.account['login'])

    def handle_line(self, line):
        command, params, tags, prefix = parse_irc(line)
        if command=='CLEARCHAT' and self.config['enabled'] and len(params)>1 and tags.get('target-user-id','').isdigit():
            from storage import name
            user=name(params[1]);channel=name(params[0]);uid=tags['target-user-id']
            if channel in self.wanted_channels() and self.identity_seen.get(user)!=uid:
                self.folder.mkdir(parents=True,exist_ok=True)
                event={'kind':'identity','user':user,'channel':channel,'user_id':uid,'at_ms':int(tags.get('tmi-sent-ts') or time.time()*1000),'source':'twitch'}
                with (self.folder/'identities.jsonl').open('a',encoding='utf-8') as file:file.write(json.dumps(event)+'\n')
                self.identity_seen[user]=uid
        if command == 'PING':self.send('PONG :' + (params[-1] if params else 'tmi.twitch.tv'))
        elif command == 'RECONNECT':raise OSError('Reconnect requested')
        elif command == '001':self.ready = True;self.auth_failures=0;self.refresh_rejections=0;self.report(phase='connected', text='Соединение установлено; подключаем каналы…',auth_retries=0,auth_reason='')
        elif command in ('ROOMSTATE','USERSTATE') and params:
            self.confirm_channel(params[0].lstrip('#').lower())
        elif command in ('JOIN','PART') and params and self.account and prefix.split('!',1)[0].lower()==self.account.get('login','').lower():
            channel=params[0].lstrip('#').lower()
            if command=='JOIN':self.confirm_channel(channel)
            else:self.retry_channel(channel,'server_part')
        elif command == 'NOTICE':
            if tags.get('msg-id') in ('login_authentication_failed', 'improperly_formatted_auth') or (params and params[-1] in ('Login authentication failed', 'Improperly formatted auth')):
                raise TwitchError(401,'IRC отклонил токен доступа.',source='irc')
            if params and params[0].startswith('#'):
                channel=params[0][1:].lower();notice=tags.get('msg-id','unknown')
                if channel in self.wanted_channels():
                    self.connection_event('notice',channel,notice)
                    # NOTICE also includes mode changes and permission to SEND.
                    # Neither implies that this read-only subscription was lost.
                    if notice in ('msg_channel_suspended','tos_ban'):self.retry_channel(channel,notice,unavailable=True)
        elif command in ('CLEARCHAT', 'CLEARMSG') and self.replay and self.config['enabled']:
            if params and params[0].lstrip('#').lower() in self.replay.wanted():
                at=int(tags.get('tmi-sent-ts') or time.time()*1000)
                event={'channel':params[0].lstrip('#').lower(),'at':at,'user':tags.get('login',''),'id':tags.get('target-msg-id','')}
                if command=='CLEARMSG':event.update(kind='delete',text=params[-1] if len(params)>1 else '')
                else:
                    event.update(kind=('timeout' if tags.get('ban-duration') else 'ban') if len(params)>1 else 'clear',user=params[1].lower() if len(params)>1 else '',duration=int(tags.get('ban-duration') or 0))
                self.replay.push(event,tags)
            if command=='CLEARMSG' and params and params[0].lstrip('#').lower() in self.config['channels']:
                event=self.deletions.event(params,tags)
                if event and self.deletions.append(event):self.report(deleted=self.snapshot()['deleted']+1)
        elif command == 'CLEARMSG':
            if self.config['enabled'] and params and params[0].lstrip('#').lower() in self.config['channels']:
                event = self.deletions.event(params, tags)
                if event and self.deletions.append(event):self.report(deleted=self.snapshot()['deleted'] + 1)
        elif command == 'USERNOTICE' and self.replay and self.config['enabled'] and params:
            self.replay.push({'kind':'notice','channel':params[0].lstrip('#').lower(),'at':int(tags.get('tmi-sent-ts') or time.time()*1000),
                'id':tags.get('id',''),'user':tags.get('login',''),'text':tags.get('system-msg','')+(' · '+params[1] if len(params)>1 else '')},tags)
        elif command == 'PRIVMSG':
            record = chat_record(params, tags, prefix)
            if record and self.config['enabled'] and record['channel'] in self.wanted_channels():self.evidence.chat(record,tags)
            if record and record['channel'] not in self.joined:self.confirm_channel(record['channel'])
            if record and self.replay and self.config['enabled']:
                self.replay.push(dict(record,kind='message',at=int(tags['tmi-sent-ts']),color=tags.get('color',''),raw_text=params[1]),tags)
            if record and self.config['enabled'] and record['channel'] in self.config['channels']:
                self.deletions.remember(record)
            if record and self.config['enabled'] and record['channel'] in self.config['channels'] and self.users.get(record['user']):
                if self.writer.append(record):self.report(saved=self.snapshot()['saved'] + 1)

    def read_chat(self):
        wanted = set(self.wanted_channels())
        for channel in sorted((self.joined | set(self.pending)) - wanted):
            self.send('PART #' + channel);self.joined.discard(channel);self.pending.pop(channel, None)
            if self.replay:self.replay.connection(self.joined)
        for channel in list(self.failed-wanted):
            self.failed.discard(channel);self.join_retries.pop(channel,None);self.join_attempts.pop(channel,None);self.channel_errors.pop(channel,None)
        self.report(joined=sorted(self.joined))
        for channel, since in list(self.pending.items()):
            if self.clock()-since>30:self.retry_channel(channel,'join_timeout')
        if self.ready and self.clock() >= self.next_join:
            missing = sorted(ch for ch in wanted-self.joined-set(self.pending) if self.clock()>=self.join_retries.get(ch,0))
            if missing:
                channel = missing[0];self.send('JOIN #' + channel);self.pending[channel] = self.clock();self.next_join = self.clock() + 2.2
                self.connection_event('join_requested',channel)
        if self.failed:self.report(text='Ожидаем подключение: ' + ', '.join('#' + x for x in sorted(self.failed))+' · повторяем автоматически')
        elif self.ready:self.report(text=f"Twitch @{self.account['login']} · подключено {len(self.joined)}/{len(wanted)} каналов")
        if self.clock() - self.last_traffic > (180 if self.ready else 25):raise OSError('Chat heartbeat expired')
        try:chunk = self.connection.recv(65536)
        except socket.timeout:return
        if not chunk:raise OSError('Chat disconnected')
        self.last_traffic = self.clock();self.buffer += chunk
        if len(self.buffer) > 2 * 1024 * 1024:raise OSError('Oversize IRC input')
        while b'\r\n' in self.buffer:
            line, self.buffer = self.buffer.split(b'\r\n', 1)
            self.handle_line(line.decode('utf-8', errors='replace'))

    def run(self):
        try:
            self.writer = MessageWriter(self.folder)
            try:self.account = self.credentials.load()
            except OSError as exc:self.report(phase='error', text=str(exc))
            while not self.stopped.is_set():
                try:
                    self.persist_status()
                    self.wake_check()
                    self.process_commands()
                    if self.auth:self.poll_auth();self.stopped.wait(.15);continue
                    if not self.account or self.auth_blocked:self.stopped.wait(.2);continue
                    if self.clock() < self.next_retry:self.stopped.wait(.2);continue
                    self.ensure_token()
                    if not self.config['enabled'] or not self.wanted_channels():
                        self.close_socket();self.report(phase='paused', text='Twitch подключён · запись выключена' if not self.config['enabled'] else 'Twitch подключён · выбери каналы');self.stopped.wait(.3);continue
                    if not self.connection:self.connect()
                    self.read_chat()
                except TwitchError as exc:
                    self.auth_failure(exc)
                except (socket.timeout, ssl.SSLError, ConnectionError) as exc:
                    self.close_socket();self.report(phase='reconnecting', text='Соединение с Twitch прервано; повторим через 15 секунд.');self.next_retry = self.clock() + 15
                except OSError:
                    self.close_socket();self.report(phase='error', text='Ошибка соединения или записи на диск. Проверь интернет и свободное место; повтор через 15 секунд.');self.next_retry = self.clock() + 15
                except (ValueError, KeyError, TypeError):
                    self.close_socket();self.report(phase='error', text='Не удалось обработать ответ Twitch. Повтор через 15 секунд.');self.next_retry = self.clock() + 15
        except Exception:
            self.report(phase='error', text='Сборщик Twitch остановился. Перезапусти архив; ранее сохранённые сообщения остаются.')
        finally:
            self.close_socket()
            if self.stopped.is_set():self.report(phase='stopped', text='Архив остановлен')
            self.persist_status(force=True)
