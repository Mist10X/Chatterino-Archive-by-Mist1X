"""Exact-user history assembled in the existing background database worker."""
import datetime as dt,json,sqlite3,time,bisect
from contextlib import closing
from pathlib import Path
from storage import name

def millis(value):
    try:return int(dt.datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()*1000)
    except (ValueError,TypeError,AttributeError,OverflowError):return 0

def event_message(row):
    """Return the message represented by a moderation row, preferring its exact evidence."""
    if row.get('kind')=='delete':
        message=row.get('message')
        if message and message.get('state')=='available' and isinstance(message.get('text'),str):return message
    context=[m for m in row.get('context',[]) if isinstance(m,dict) and isinstance(m.get('text'),str) and m.get('text').strip()]
    if not context:return None
    # Preserve source order when timestamps are missing or equal. AutoMod wins at the
    # same instant because it is the closest available evidence for that punishment.
    return max(enumerate(context),key=lambda pair:(millis(pair[1].get('time_utc','')),bool(pair[1].get('automod')),pair[0]))[1]

def event_summary(row,limit=220):
    message=event_message(row)
    if not message:return 'Сообщение недоступно'
    value=' '.join(message['text'].split())
    if message.get('automod'):value='AutoMod · '+value
    if len(value)>limit:value=value[:limit-1].rstrip()+'…'
    return value or 'Сообщение недоступно'

def event_table_summary(row,limit=220):
    prefix='Заменён новым наказанием · ' if row.get('status')=='Заменён новым наказанием' else ''
    return prefix+event_summary(row,max(1,limit-len(prefix)))

class UserHistory:
    def __init__(self,archive,moderation):self.archive=archive;self.mod=moderation;self.signature=None;self.rows=[]
    def query(self,user,mode='history',channel='',search='',offset=0,limit=100):
        user=name(user)
        if not user:raise ValueError('Введи ник Twitch.')
        paths=list((self.archive.data/'replays/streams').glob('*/chat.sqlite3'))
        files=[]
        for p in paths:
            for f in (p,Path(str(p)+'-wal')):
                try:files.append((str(f),f.stat().st_size,f.stat().st_mtime_ns))
                except OSError:pass
        signature=(user,self.archive.db.total_changes,self.mod.db.total_changes,tuple(files))
        if signature!=self.signature:
            rows={};messages={};warnings=[]
            def message(m,origin):
                if m.get('user')!=user or not m.get('channel') or not isinstance(m.get('text'),str):return
                at=millis(m.get('time_utc',''))
                key='message|'+m['channel']+'|'+str(m.get('id') or (str(at)+'|'+m['text']))
                if key in messages:
                    if m.get('reply') and not messages[key]['message'].get('reply'):messages[key]['message']['reply']=m['reply']
                    messages[key]['message']['automod']=messages[key]['message'].get('automod',False) or m.get('automod',False)
                    return
                messages[key]={'key':key,'kind':'message','user':user,'channel':m['channel'],'at_ms':at,'message':m,'origin':origin}
            for r in self.archive.db.execute('SELECT * FROM messages WHERE user=?',(user,)):
                m=dict(r);m['reply']=json.loads(m['reply']) if m['reply'] else None;message(m,'Наш архив')
            for p in paths:
                try:
                    meta=json.loads((p.parent/'stream.json').read_text(encoding='utf-8'))
                    with closing(sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True,timeout=1)) as db:
                        for payload,at in db.execute("SELECT payload,at FROM events WHERE user=? AND kind='message'",(user,)):
                            m=json.loads(payload);m['channel']=meta['channel'];m['time_utc']=dt.datetime.fromtimestamp(at/1000,dt.timezone.utc).isoformat(timespec='milliseconds')
                            message(m,'Повтор чата')
                except (OSError,ValueError,KeyError,sqlite3.Error):warnings.append('Один из повторов временно недоступен; обнови карточку позже.')
            contexts=[]
            for raw in self.mod.db.execute("SELECT * FROM events WHERE user=? AND kind IN ('ban','timeout','delete','unban','untimeout','speech')",(user,)):
                row=dict(raw);row['context']=json.loads(row['context']);row['message']=json.loads(row['message']) if row['message'] else None
                row['status'],row['active']=self.mod.status(raw);self.mod.combined.enrich(row);rows[row['key']]=row
                contexts.extend(row['context'])
                if row['kind']=='delete' and row['message'] and row['message'].get('state')=='available':contexts.append(row['message'])
            # Context messages may lack a Twitch ID; join only a unique text/time match.
            by_text={}
            for row in messages.values():by_text.setdefault((row['channel'],row['message']['text']),[]).append(row)
            for m in contexts:
                candidates=[r for r in by_text.get((m.get('channel'),m.get('text')),[]) if abs(r['at_ms']-millis(m.get('time_utc','')))<1000]
                if len(candidates)==1:
                    candidates[0]['message']['automod']=candidates[0]['message'].get('automod',False) or m.get('automod',False)
                else:message(m,'Контекст наказания'+(' · Chatterino+' if m.get('source')=='chatterino_shared' else ''))
            by_channel={}
            for item in messages.values():by_channel.setdefault(item['channel'],[]).append(item)
            times={}
            for channel_name,items in by_channel.items():
                items.sort(key=lambda m:m['at_ms']);times[channel_name]=[m['at_ms'] for m in items]
            for row in rows.values():
                if row['kind'] not in ('ban','timeout'):continue
                position=bisect.bisect_right(times.get(row['channel'],[]),row['at_ms'])-1
                if position>=0:
                    last=by_channel[row['channel']][position]
                    if not row['context'] or last['at_ms']>max(millis(m.get('time_utc','')) for m in row['context']):row['context'].append(last['message'])
            rows.update(messages);self.rows=sorted(rows.values(),key=lambda r:(r['at_ms'],r['key']),reverse=True)
            self.warnings=warnings;self.signature=signature
        # Current timeout/status can change even when files stay unchanged.
        for row in self.rows:
            if row['kind']!='message':row['status'],row['active']=self.mod.status(row)
        matching=[r for r in self.rows if (mode=='history' or (mode=='messages' and r['kind']=='message') or (mode=='bans' and r['kind']=='ban'))
            and (not channel or r['channel']==channel)
            and (not search or search.casefold() in self.search_text(r).casefold())]
        return {'rows':matching[offset:offset+limit],'total':len(matching),'counts':self.mod.totals(user),
            'messages':sum(r['kind']=='message' for r in self.rows),'channels':sorted({r['channel'] for r in self.rows}),
            'warning':' '.join(sorted(set(self.warnings)))}
    @staticmethod
    def search_text(row):
        message=row.get('message') or {}
        return ' '.join([row.get('status',''),row.get('raw',''),message.get('text',''),*[m['text'] for m in row.get('context',[])]])

def event_blocks(row):
    from panel import local_time
    from moderation_view import stamp
    from emote_widgets import message_blocks
    if row['kind']=='message':
        return [{'text':row['origin'],'style':'meta'}]+([{'text':'AutoMod: сообщение было задержано','style':'deletion'}] if row['message'].get('automod') else [])+message_blocks(row['message'],local_time)
    labels={'ban':'БАН','timeout':'МУТ','delete':'УДАЛЕНИЕ СООБЩЕНИЯ','unban':'РАЗБАН','untimeout':'СНЯТИЕ МУТА','speech':'ПОЛЬЗОВАТЕЛЬ СНОВА ПИШЕТ'}
    blocks=[{'text':labels[row['kind']]+' · @'+row['user']+' · #'+row['channel'],'style':'title'},
        {'text':stamp(row['at_ms'])+' · '+row.get('origin','Наш архив'),'style':'meta'}]
    if row['kind'] in ('ban','timeout','delete'):blocks.append({'text':row.get('status',''),'style':'meta'})
    if row.get('duration') is not None:blocks.append({'text':f"Длительность: {row['duration']} с",'style':'meta'})
    if row.get('shared_fetched_at'):blocks.append({'text':'Данные Chatterino+ получены: '+stamp(row['shared_fetched_at']*1000),'style':'meta'})
    context=sorted(row.get('context',[]),key=lambda m:millis(m.get('time_utc','')))
    if row['kind']=='delete' and row.get('message'):context=[row['message']]
    if row['kind']=='ban':
        blocks.append({'text':'Последнее доступное сообщение перед баном','style':'title'})
        if context:
            last=context[-1]
            if last.get('automod'):blocks.append({'text':'AutoMod: сообщение было задержано','style':'deletion'})
            blocks.extend(message_blocks(last,local_time))
        else:blocks.append({'text':'Сообщение недоступно.','style':'body'})
        context=context[:-1]
        if context:blocks.append({'text':'Другие доступные сообщения перед баном','style':'meta'})
    for m in context:
        if m.get('automod'):blocks.append({'text':'AutoMod: сообщение было задержано','style':'deletion'})
        blocks.extend(message_blocks(m,local_time))
    if row.get('raw'):blocks.append({'text':row['raw'],'style':'meta'})
    if row['kind']=='ban':blocks.append({'text':'Сообщение перед баном не обязательно является его причиной. Отсутствие записи в ответе сервиса не подтверждает разбан.','style':'meta'})
    return blocks
