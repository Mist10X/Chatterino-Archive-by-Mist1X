"""Read-only Twitch chat, public device OAuth and Windows-bound credential storage."""
from __future__ import annotations
import ctypes
from ctypes import wintypes
import datetime as dt
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from storage import atomic_write, name

CLIENT_ID = '4qfqqxerjxct8iahgq3nq7yiwd33ce'
SCOPES = ['chat:read']


class TwitchError(Exception):
    def __init__(self, code, message='', source=''):
        self.code = code;self.source=source
        super().__init__(message or 'Twitch: ' + str(code))


class API:
    def request(self, path, fields=None, token=None):
        # No arbitrary hosts, redirects or credentials in URLs/logs.
        if path not in ('device', 'token', 'validate', 'revoke'):
            raise ValueError('Unknown OAuth endpoint')
        headers = {'Accept': 'application/json', 'User-Agent': 'Mist1XArchive/0.9.0'}
        if token:
            headers['Authorization'] = 'OAuth ' + token
        payload = urllib.parse.urlencode(fields).encode() if fields is not None else None
        req = urllib.request.Request('https://id.twitch.tv/oauth2/' + path, payload, headers)
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):return None
        try:
            with urllib.request.build_opener(NoRedirect).open(req, timeout=10) as response:
                body = response.read(100000)
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            try:message = str(json.loads(exc.read(10000)).get('message', ''))
            except (ValueError, OSError):message = ''
            known = ('authorization_pending', 'slow_down', 'access_denied', 'expired_token', 'invalid device code')
            refresh=path=='token' and fields and fields.get('grant_type')=='refresh_token'
            folded=message.casefold()
            if refresh and ('missing client secret' in folded or 'client secret is missing' in folded):
                raise TwitchError('client_type','Для обновления входа Twitch тип приложения должен быть Public.',source='refresh') from None
            if refresh and ('invalid refresh token' in folded or exc.code==401):
                raise TwitchError('invalid_refresh','Twitch подтвердил, что сохранённый вход больше недействителен.',source='refresh') from None
            if path=='validate' and exc.code==401:
                raise TwitchError(401,'Токен доступа Twitch нужно обновить.',source='validate') from None
            fallback=('rate_limit' if exc.code==429 else 'server' if exc.code>=500 else 'refresh_rejected') if refresh else exc.code
            code = {value.casefold():value for value in known}.get(folded,fallback)
            raise TwitchError(code,source='refresh' if refresh else path) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise TwitchError('network', 'Нет связи с Twitch. Повторим подключение автоматически.',source=path) from None


class Credentials:
    """DPAPI CurrentUser; never writes plaintext tokens or machine-wide secrets."""
    def __init__(self, folder):self.path = Path(folder) / 'account.dpapi'

    @staticmethod
    def crypt(payload, decrypt=False):
        if os.name != 'nt':raise OSError('Для хранения входа нужны средства защиты Windows.')
        class Blob(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
        buffer = (ctypes.c_ubyte * len(payload)).from_buffer_copy(payload)
        source = Blob(len(payload), buffer); target = Blob()
        library = ctypes.WinDLL('crypt32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p];kernel.LocalFree.restype = ctypes.c_void_p
        operation = library.CryptUnprotectData if decrypt else library.CryptProtectData
        operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                              ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        operation.restype = wintypes.BOOL
        if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
            raise OSError('Не удалось открыть сохранённый вход Twitch в этой учётной записи Windows.')
        try:return ctypes.string_at(target.data, target.size)
        finally:kernel.LocalFree(target.data)

    def save(self, value):
        # atomic_write handles replace retries; ciphertext is harmless base16 text.
        atomic_write(self.path, self.crypt(json.dumps(value).encode()).hex())

    def load(self):
        if not self.path.exists():return None
        try:return json.loads(self.crypt(bytes.fromhex(self.path.read_text()), True))
        except (ValueError, KeyError):raise OSError('Сохранённый вход Twitch повреждён. Подключи аккаунт заново.') from None

    def clear(self):self.path.unlink(missing_ok=True)


def settings_load(folder):
    path = Path(folder) / 'settings.json'
    if not path.exists():return {'enabled': False, 'channels': [], 'tray': True}
    value = json.loads(path.read_text(encoding='utf-8'))
    return settings_validate(value)


def settings_validate(value):
    channels = sorted({name(ch) for ch in value.get('channels', [])})
    if len(channels) > 100:raise ValueError('Можно выбрать до 100 каналов; Twitch также учитывает другие клиенты аккаунта.')
    result={'enabled': value.get('enabled') is True, 'channels': channels, 'tray': value.get('tray', True) is True}
    if value.get('moderation_details'):result['moderation_details']=True
    return result


def settings_save(folder, value):
    value = settings_validate(value)
    atomic_write(Path(folder) / 'settings.json', json.dumps(value, ensure_ascii=False, indent=2))
    return value


def parse_irc(line):
    tags = {}; prefix = ''
    if line.startswith('@'):
        tag_text, _, line = line.partition(' ')
        escapes = {'s': ' ', ':': ';', 'r': '\r', 'n': '\n', '\\': '\\'}
        for tag in tag_text[1:].split(';'):
            key, _, value = tag.partition('=')
            tags[key] = re.sub(r'\\(.)', lambda m: escapes.get(m[1], m[1]), value)
    if line.startswith(':'):prefix, _, line = line[1:].partition(' ')
    head, separator, trailing = line.partition(' :')
    parts = head.split()
    if not parts:return '', [], {}, ''
    return parts[0], parts[1:] + ([trailing] if separator else []), tags, prefix


def chat_record(params, tags, prefix):
    if len(params) != 2 or not tags.get('id'):return None
    user = name(prefix.split('!', 1)[0]);channel = name(params[0])
    at = int(tags.get('tmi-sent-ts', '0'))
    if at <= 0:return None
    text = params[1]
    if text.startswith('\x01ACTION ') and text.endswith('\x01'):text = '/me ' + text[8:-1]
    record = {'user': user, 'user_id': tags.get('user-id', ''), 'channel': channel,
              'channel_id': tags.get('room-id', ''), 'id': tags['id'],
              'time_utc': dt.datetime.fromtimestamp(at / 1000, dt.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
              'display_name': tags.get('display-name') or user, 'text': text, 'source': 'twitch-irc'}
    if tags.get('reply-parent-msg-id'):
        record['reply'] = {'state': 'available' if 'reply-parent-msg-body' in tags else 'unavailable',
                           'id': tags['reply-parent-msg-id'], 'channel': channel,
                           'user_id': tags.get('reply-parent-user-id', ''),
                           'user': tags.get('reply-parent-user-login', ''),
                           'display_name': tags.get('reply-parent-display-name', ''),
                           'text': tags.get('reply-parent-msg-body', '')}
    return record


class MessageWriter:
    """Only this process writes twitch/messages; plugin JSONL files are untouched."""
    def __init__(self, folder):
        self.folder = Path(folder) / 'messages';self.folder.mkdir(parents=True, exist_ok=True)
        self.seen = set();self.order = deque();self.checked = set()

    def append(self, record):
        key = (record['channel'], record['id'])
        if key in self.seen:return False
        path = self.folder / (name(record['user']) + '--' + name(record['channel']) + '.jsonl')
        if path not in self.checked:
            # Recover only our own incomplete last record, preserving its bytes.
            if path.exists() and path.stat().st_size:
                with path.open('r+b') as f:
                    size = f.seek(0, 2);start = max(0, size - 2 * 1024 * 1024);f.seek(start);tail = f.read()
                    if not tail.endswith(b'\n'):
                        end = tail.rfind(b'\n') + 1
                        if not end and start:raise OSError('Неполная строка архива Twitch слишком велика.')
                        fragment = tail[end:]
                        recovery = path.with_suffix('.partial-' + str(__import__('time').time_ns()))
                        recovery.write_bytes(fragment);f.seek(start + end);f.truncate();f.flush();os.fsync(f.fileno())
            self.checked.add(path)
        with path.open('ab') as f:
            f.write((json.dumps(record, ensure_ascii=False, separators=(',', ':')) + '\n').encode())
            f.flush();os.fsync(f.fileno())
        self.seen.add(key);self.order.append(key)
        if len(self.order) > 20000:self.seen.discard(self.order.popleft())
        return True
