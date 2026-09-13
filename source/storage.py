"""Local archive storage and a deliberately non-executable Lua control protocol."""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path

NAME = re.compile(r"[a-z0-9_]{1,64}\Z")
ARCHIVE = re.compile(r"([a-z0-9_]{1,64})--([a-z0-9_]{1,64})\.jsonl\Z")


def name(value):
    value = value.strip().lower().lstrip("@#")
    if not NAME.fullmatch(value):
        raise ValueError("Ник или канал: только латинские буквы, цифры и подчёркивание (до 64 символов).")
    return value


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(6):
            try:
                os.replace(temp, path)
                return
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(.03 * (attempt + 1))
    finally:
        temp.unlink(missing_ok=True)


def layout_channels(profile):
    found = set()
    def walk(obj):
        if isinstance(obj, dict):
            if obj.get("type") == "twitch" and isinstance(obj.get("name"), str):
                try:
                    found.add(name(obj["name"]))
                except ValueError:
                    pass
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)
    layout = Path(profile) / "Settings" / "window-layout.json"
    if layout.exists():
        walk(json.loads(layout.read_text(encoding="utf-8-sig")))
    return sorted(found)


class Control:
    def __init__(self, data):
        self.data = Path(data)
        self.path = self.data / "panel-control.txt"
        self.users = {}
        self.channels = []
        self.excluded = set()
        self.keep = 1000
        self.auto_trim = True
        self.auto_trim_interval = 3600
        self.auto_trim_keep = 1000
        self.trim_preserved = {"dangerlyoha", "morphe_ya"}
        self.revision = ""
        if self.path.exists():
            self.load()
        else:
            for path in sorted(self.data.glob("*.jsonl")):
                match = ARCHIVE.fullmatch(path.name)
                if not match:
                    continue
                user, channel = match.groups()
                enabled = False
                state = path.with_suffix(".state")
                if state.exists():
                    for line in state.read_text(encoding="utf-8").splitlines():
                        if line in ("on", "off"):
                            enabled = line == "on"
                self.users[user] = self.users.get(user, False) or enabled
                self.channels.append(channel)
            self.channels = sorted(set(self.channels))

    def load(self):
        lines = self.path.read_text(encoding="utf-8").splitlines()
        if not lines or lines[0] != "HT2" or lines[-1] != "END":
            raise ValueError("Настройки панели повреждены. Сохранённые сообщения не затронуты.")
        users, channels, revision, keep = {}, [], None, 1000
        # Older configurations did not contain automatic trimming fields. The
        # first upgraded run enables the requested safe defaults.
        auto_trim, auto_trim_interval, auto_trim_keep = True, 3600, 1000
        trim_preserved = {"dangerlyoha", "morphe_ya"}
        trim_preserve_seen = False
        excluded = set()
        for line in lines[1:-1]:
            fields = line.split("\t")
            if len(fields) == 2 and fields[0] == "revision":
                revision = fields[1]
            elif len(fields) == 2 and fields[0] == "keep":
                keep = int(fields[1])
            elif len(fields) == 3 and fields[0] == "user" and fields[2] in ("on", "off"):
                users[name(fields[1])] = fields[2] == "on"
            elif len(fields) == 2 and fields[0] == "channel":
                channels.append(name(fields[1]))
            elif len(fields) == 2 and fields[0] == "exclude":
                excluded.add(name(fields[1]))
            elif len(fields) == 2 and fields[0] == "auto_trim" and fields[1] in ("on", "off"):
                auto_trim = fields[1] == "on"
            elif len(fields) == 2 and fields[0] == "auto_trim_interval":
                auto_trim_interval = int(fields[1])
            elif len(fields) == 2 and fields[0] == "auto_trim_keep":
                auto_trim_keep = int(fields[1])
            elif len(fields) == 2 and fields[0] == "trim_preserve":
                if not trim_preserve_seen:trim_preserved.clear();trim_preserve_seen=True
                trim_preserved.add(name(fields[1]))
            elif len(fields) == 2 and fields[0] == "trim_preserve_count":
                if int(fields[1]) < 0 or int(fields[1]) > 1000:raise ValueError("Invalid trim preserve count")
                if not trim_preserve_seen:trim_preserved.clear();trim_preserve_seen=True
            else:
                raise ValueError("Неизвестная строка настроек панели.")
        if (not revision or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", revision)
                or not 1 <= keep <= 1000000 or not 60 <= auto_trim_interval <= 86400
                or not 1 <= auto_trim_keep <= 1000000):
            raise ValueError("Недопустимая версия настроек или размер истории.")
        self.users, self.channels, self.keep, self.revision = users, sorted(set(channels)-excluded), keep, revision
        self.excluded = excluded
        self.auto_trim, self.auto_trim_interval, self.auto_trim_keep = auto_trim, auto_trim_interval, auto_trim_keep
        self.trim_preserved = trim_preserved

    def save(self):
        revision = uuid.uuid4().hex
        lines = ["HT2", "revision\t" + revision, "keep\t" + str(self.keep),
            "auto_trim\t" + ("on" if self.auto_trim else "off"),
            "auto_trim_interval\t" + str(self.auto_trim_interval),
            "auto_trim_keep\t" + str(self.auto_trim_keep)]
        lines += [f"user\t{name(user)}\t{'on' if enabled else 'off'}" for user, enabled in sorted(self.users.items())]
        lines += [f"channel\t{name(channel)}" for channel in sorted(set(self.channels))]
        lines += [f"exclude\t{channel}" for channel in sorted(self.excluded)]
        lines += [f"trim_preserve_count\t{len(self.trim_preserved)}"]
        lines += [f"trim_preserve\t{name(channel)}" for channel in sorted(self.trim_preserved)]
        atomic_write(self.path, "\n".join(lines + ["END", ""]))
        self.revision = revision
        self.channels = sorted(set(self.channels))

    def remove_user(self, user):
        previous = dict(self.users)
        self.users.pop(name(user), None)
        try:
            self.save()
        except Exception:
            self.users = previous
            raise

    def request_trim(self, channel):
        request = self.data / "panel-request.txt"
        pending = self.pending_request()
        if pending:
            raise ValueError("Предыдущая очистка ещё не подтверждена. Дождись ответа Chatterino или отмени её.")
        token = uuid.uuid4().hex
        atomic_write(request, f"HT2\ntrim\t{token}\t{name(channel)}\t{self.keep}\nEND\n")
        return token

    def pending_request(self):
        path = self.data / "panel-request.txt"
        if not path.exists():
            return None
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) < 2:return None
        fields = lines[1].split("\t")
        if len(fields) < 2:return None
        ack = self.data / "panel-ack.json"
        try:
            answer = json.loads(ack.read_text(encoding="utf-8"))
            if answer.get("id") == fields[1] and answer.get('status') in ('ok','error','cancelled'):
                return None
        except (OSError, ValueError):
            pass
        return fields

    def request_trim_except(self, channels, excluded):
        if self.pending_request():
            raise ValueError('Предыдущая очистка ещё выполняется или ожидает ответа Chatterino.')
        channels = sorted({name(ch) for ch in channels});excluded = sorted({name(ch) for ch in excluded})
        if not set(excluded) <= set(channels) or not set(channels)-set(excluded):
            raise ValueError('Нет каналов для очистки или список исключений не соответствует каналам.')
        if len(channels)>1000:raise ValueError('Слишком много каналов для одного запроса.')
        token = uuid.uuid4().hex
        lines = ['HT2',f'trim_except\t{token}\t1000'] + ['channel\t'+ch for ch in channels] + ['exclude\t'+ch for ch in excluded]
        atomic_write(self.data/'panel-request.txt','\n'.join(lines+['END','']))
        return token

    def status(self):
        path = self.data / "panel-status.json"
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
            result["fresh"] = time.time() - path.stat().st_mtime < 9
            return result
        except (OSError, ValueError):
            return {"fresh": False, "channels": [], "error": ""}


class ArchiveIndex:
    """Incremental disposable index. JSONL originals are never rewritten/deleted."""
    def __init__(self, data):
        self.data = Path(data)
        cache = self.data / "panel-cache"
        cache.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(cache / "archive.sqlite3", timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS messages (
                user TEXT, channel TEXT, id TEXT, time_utc TEXT,
                display_name TEXT, text TEXT, source TEXT, user_id TEXT DEFAULT '',
                PRIMARY KEY(user,channel,id));
            CREATE INDEX IF NOT EXISTS message_time ON messages(user,time_utc DESC);
            CREATE INDEX IF NOT EXISTS message_channel_time ON messages(user,channel,time_utc DESC);
            CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, offset INTEGER, invalid INTEGER DEFAULT 0);
        """)
        if 'reply' not in {row['name'] for row in self.db.execute('PRAGMA table_info(messages)')}:
            self.db.execute('ALTER TABLE messages ADD COLUMN reply TEXT')
            self.db.commit()
        if 'user_id' not in {row['name'] for row in self.db.execute('PRAGMA table_info(messages)')}:
            self.db.execute("ALTER TABLE messages ADD COLUMN user_id TEXT DEFAULT ''")
            # Re-read only our direct Twitch files once. Their JSONL rows already
            # contain the IDs and enrich the disposable index in place.
            self.db.execute("UPDATE files SET offset=0 WHERE path LIKE 'twitch/messages/%'")
            self.db.commit()

    def sync(self, max_seconds=None):
        inserted = 0
        deadline = time.monotonic()+max_seconds if max_seconds is not None else float('inf')
        paths = sorted(p for p in list(self.data.glob('*.jsonl')) + list((self.data/'twitch/messages').glob('*.jsonl')) if ARCHIVE.fullmatch(p.name))
        start = getattr(self, '_sync_cursor', 0) % max(1,len(paths))
        for step in range(len(paths)):
            pos=(start+step)%len(paths);path=paths[pos]
            self._sync_cursor=(pos+1)%len(paths)
            match = ARCHIVE.fullmatch(path.name)
            if not match:
                continue
            expected_user, expected_channel = match.groups()
            file_key = path.relative_to(self.data).as_posix()
            row = self.db.execute("SELECT offset,invalid FROM files WHERE path=?", (file_key,)).fetchone()
            offset, invalid = (row[0], row[1]) if row else (0, 0)
            size=path.stat().st_size
            if row is not None and size==offset:
                continue
            if size < offset:
                offset, invalid = 0, 0
            with path.open("rb") as f, self.db:
                f.seek(offset)
                # Bound each pass so a large existing archive cannot monopolize the worker.
                for i in range(20000):
                    if i and i%32==0 and time.monotonic()>=deadline:break
                    line = f.readline(2 * 1024 * 1024)
                    if not line or not line.endswith(b"\n"):
                        break
                    offset = f.tell()
                    if not line.strip():
                        continue
                    try:
                        item = json.loads(line)
                        if (name(item["user"]) != expected_user or name(item["channel"]) != expected_channel
                                or not isinstance(item["id"], str) or not item["id"]
                                or not isinstance(item["text"], str)):
                            raise ValueError("Invalid record")
                        reply=item.get('reply')
                        if reply is not None:
                            if (not isinstance(reply,dict) or reply.get('state') not in ('available','unavailable')
                                or reply.get('channel')!=expected_channel or not isinstance(reply.get('text'),str)):
                                invalid+=1;reply=None
                        uid=str(item.get('user_id',''))
                        if not re.fullmatch(r'[0-9]{1,25}',uid):uid=''
                        if reply is not None:
                            reply_uid=str(reply.get('user_id',''))
                            reply['user_id']=reply_uid if re.fullmatch(r'[0-9]{1,25}',reply_uid) else ''
                        inserted += self.db.execute("""INSERT INTO messages (user,channel,id,time_utc,display_name,text,source,reply,user_id)
                            VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(user,channel,id) DO UPDATE SET
                            user_id=CASE WHEN excluded.user_id<>'' THEN excluded.user_id ELSE messages.user_id END,
                            reply=CASE WHEN excluded.reply IS NOT NULL AND (messages.reply IS NULL OR
                              (json_extract(messages.reply,'$.state')='unavailable' AND json_extract(excluded.reply,'$.state')='available'))
                              THEN excluded.reply ELSE messages.reply END
                            WHERE (excluded.user_id<>'' AND messages.user_id='') OR
                              (excluded.reply IS NOT NULL AND (messages.reply IS NULL OR
                              (json_extract(messages.reply,'$.state')='unavailable' AND json_extract(excluded.reply,'$.state')='available')))""", (
                            expected_user, expected_channel, item["id"], str(item.get("time_utc", "unknown")),
                            str(item.get("display_name", expected_user)), item["text"], str(item.get("source", "")),
                            json.dumps(reply,ensure_ascii=False) if reply is not None else None,uid
                        )).rowcount
                    except (ValueError, KeyError, TypeError):
                        invalid += 1
                self.db.execute("INSERT OR REPLACE INTO files VALUES (?,?,?)", (file_key, offset, invalid))
            if time.monotonic()>=deadline:break
        return inserted

    def where(self, user, channel="", search="", day=""):
        terms, args = ["user=?"], [name(user)]
        if channel:
            terms.append("channel=?")
            args.append(name(channel))
        if search:
            terms.append("instr(lower(text),lower(?))>0")
            # Python casefold handles Cyrillic, unlike SQLite's ASCII-only lower().
            self.db.create_function("lower", 1, lambda s: s.casefold() if s else s, deterministic=True)
            args.append(search)
        if day:
            selected = dt.date.fromisoformat(day)
            start = dt.datetime.combine(selected, dt.time()).astimezone(dt.timezone.utc)
            end = dt.datetime.combine(selected + dt.timedelta(days=1), dt.time()).astimezone(dt.timezone.utc)
            terms += ["time_utc>=?", "time_utc<?"]
            args += [start.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                     end.isoformat(timespec="milliseconds").replace("+00:00", "Z")]
        return " AND ".join(terms), args

    def query(self, user, channel="", search="", day="", offset=0, limit=100):
        where, args = self.where(user, channel, search, day)
        total = self.db.execute("SELECT count(*) FROM messages WHERE " + where, args).fetchone()[0]
        rows = self.db.execute("SELECT * FROM messages WHERE " + where +
                               " ORDER BY time_utc DESC,id DESC LIMIT ? OFFSET ?", args + [limit, offset]).fetchall()
        result=[]
        for row in rows:
            record=dict(row);record['reply']=json.loads(row['reply']) if row['reply'] else None;result.append(record)
        return total,result

    def counts(self):
        return dict(self.db.execute("SELECT user,count(*) FROM messages GROUP BY user").fetchall())

    def invalid_count(self):
        return self.db.execute("SELECT coalesce(sum(invalid),0) FROM files").fetchone()[0]

    def export(self, destination, user, channel="", search="", day=""):
        where, args = self.where(user, channel, search, day)
        rows = self.db.execute("SELECT time_utc,channel,display_name,text,reply FROM messages WHERE " + where +
                               " ORDER BY time_utc,id", args)
        total = 0
        with open(destination, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Время UTC", "Канал", "Пользователь", "Сообщение", "Автор исходного сообщения", "Исходное сообщение"])
            for row in rows:
                reply=json.loads(row['reply']) if row['reply'] else {}
                values=list(row)[:4]+[reply.get('display_name') or reply.get('user',''),reply.get('text','')]
                # Exported user text must not become a spreadsheet formula.
                writer.writerow(["'" + x if x.lstrip().startswith(("=", "+", "-", "@")) else x for x in values])
                total += 1
        return total

    def close(self):
        self.db.close()
