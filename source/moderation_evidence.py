"""Separate the executor from a reward buyer. Never infer causality from timing alone."""
import datetime as dt
import json,re,threading,time
from pathlib import Path
from storage import atomic_write,name

MOD_SCOPES=['moderator:read:'+s for s in ('blocked_terms','chat_settings','unban_requests','banned_users','chat_messages','warnings','moderators','vips')]
REWARD_SCOPE='channel:read:redemptions'
AUTOMOD_SCOPE='moderator:manage:automod'

def reward_target(value):
    match=re.match(r'\s*@?([A-Za-z0-9_]{1,25})(?=$|[\s,])',value or '')
    return match[1].lower() if match else ''

def millis(value):return int(dt.datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()*1000)
def rules_load(data):
    try:return json.loads((Path(data)/'reward-rules.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):return []
def rules_save(data,rules):
    clean=[]
    for r in rules:
        if r['action'] not in ('timeout','untimeout'):raise ValueError('Неизвестное действие награды.')
        template=r.get('template','').strip()
        mode=r.get('mode','bot')
        if mode not in ('bot','correlation'):raise ValueError('Неизвестный способ сопоставления.')
        if mode=='bot' and (template.count('{buyer}')!=1 or template.count('{target}')!=1):raise ValueError('В подтверждении нужны {buyer} и {target} — по одному разу.')
        duration=int(r.get('duration',0))
        if mode=='correlation' and r['action']=='timeout' and not 0<duration<=1209600:raise ValueError('Укажи длительность мута в секундах.')
        clean.append({'channel':name(r['channel']),'reward':str(r['reward']),'action':r['action'],'bot':name(r['bot']) if mode=='bot' else '',
            'template':template,'self':bool(r.get('self')) if mode=='bot' else False,'mode':mode,'duration':duration,'title':str(r.get('title',''))[:200]})
    atomic_write(Path(data)/'reward-rules.json',json.dumps(clean,ensure_ascii=False,indent=2))

class EvidenceWriter:
    def __init__(self,data):self.data=Path(data);self.lock=threading.Lock();self.rules=[];self.next_rules=0
    def append(self,event):
        path=self.data/'twitch/activity.jsonl';path.parent.mkdir(parents=True,exist_ok=True)
        with self.lock,path.open('a',encoding='utf-8') as f:f.write(json.dumps(event,ensure_ascii=False)+'\n')
    def chat(self,record,tags):
        if not record:return
        channel=record['channel'];at=millis(record['time_utc']);text=record['text']
        if time.monotonic()>=self.next_rules:self.rules=rules_load(self.data);self.next_rules=time.monotonic()+5
        if tags.get('custom-reward-id'):
            self.append({'kind':'reward','id':'irc:'+record['id'],'channel':channel,'buyer':record['user'],
                'reward':tags['custom-reward-id'],'input':text,'at_ms':at,'status':'observed'})
        configured=any(r['channel']==channel and r['bot']==record['user'] for r in self.rules)
        discover=('moderator/1' in tags.get('badges','') or record['user'] in ('streamelements','nightbot','moobot')) and re.search(r'мут|разбан|timeout|unban',text,re.I)
        if configured or discover:
            self.append({'kind':'bot_result','id':record['id'],'channel':channel,'bot':record['user'],'text':text,'at_ms':at})

class Evidence:
    def __init__(self,index):
        self.index=index;self.db=index.db;self.data=index.data
        self.db.executescript('''CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY,kind TEXT,channel TEXT,at_ms INTEGER,payload TEXT);
        CREATE INDEX IF NOT EXISTS evidence_channel_time ON evidence(channel,kind,at_ms);
        CREATE TABLE IF NOT EXISTS executors(action_key TEXT,actor TEXT,PRIMARY KEY(action_key,actor));''')
    def ingest(self,e):
        if e['kind']=='executor':
            action=e['action'];self.index.ingest(action)
            key=(action['channel']+'|delete|'+action['message_id']) if action['kind']=='delete' else f"{action['channel']}|{action['user']}|{action['kind']}|{action['at_ms']}|{action.get('duration') if action.get('duration') is not None else ''}"
            alias=self.db.execute('SELECT canonical FROM aliases WHERE key=?',(key,)).fetchone()
            self.db.execute('INSERT OR IGNORE INTO executors VALUES(?,?)',(alias[0] if alias else key,name(e['actor'])))
            return 1
        if e['kind'] not in ('reward','bot_result'):raise ValueError('Unknown evidence')
        channel=name(e['channel']);at=int(e['at_ms']);key=e['kind']+':'+str(e['id'])
        if len(key)>256 or not 0<at<253402300799999:raise ValueError('Invalid evidence')
        old=self.db.execute('SELECT payload FROM evidence WHERE id=?',(key,)).fetchone()
        if old and e['kind']=='reward':
            prev=json.loads(old[0])
            if prev.get('status') in ('fulfilled','canceled') and e.get('status') in ('observed','unfulfilled'):return 0
        payload=json.dumps(e,ensure_ascii=False)
        if old and old[0]==payload:return 0
        self.db.execute('INSERT OR REPLACE INTO evidence VALUES(?,?,?,?,?)',(key,e['kind'],channel,at,payload));return 1
    def enrich(self,row):
        row['executors']=[x[0] for x in self.db.execute('SELECT actor FROM executors WHERE action_key=?',(row['key'],))]
        row['reward_attribution']=None
        row['lifted_by']=None
        row['unmute_purchases']=[]
        stamp=row['at_ms'];channel=row['channel'];kind=row['kind']
        if kind in ('ban','timeout'):
            later=self.db.execute("SELECT * FROM events WHERE channel=? AND user=? AND kind IN ('ban','timeout','unban','untimeout') AND at_ms>? ORDER BY at_ms,sequence LIMIT 1",(channel,row['user'],stamp)).fetchone()
            if later and later['kind'] in ('unban','untimeout'):
                lifted=self.enrich(dict(later))
                if lifted['executors'] or lifted['reward_attribution']:row['lifted_by']=lifted
            if kind=='timeout' and not row['lifted_by']:
                end=min(stamp+int(row.get('duration') or 0)*1000,later['at_ms'] if later else 253402300799999)
                reward_ids={r['reward'] for r in rules_load(self.data) if r['channel']==channel and r['action']=='untimeout'}
                purchases=[json.loads(p[0]) for p in self.db.execute("SELECT payload FROM evidence WHERE channel=? AND kind='reward' AND at_ms>? AND at_ms<?",(channel,stamp,end))]
                for receipt in purchases:
                    target=reward_target(receipt['input'])
                    canceled=any(o.get('status')=='canceled' and o['buyer']==receipt['buyer'] and o['reward']==receipt['reward'] and o['input']==receipt['input'] and abs(o['at_ms']-receipt['at_ms'])<=5000 for o in purchases)
                    if target==row['user'] and receipt['reward'] in reward_ids and not canceled:row['unmute_purchases'].append(receipt['buyer'])
        if kind not in ('timeout','untimeout'):return row
        rules=[r for r in rules_load(self.data) if r['channel']==channel and r['action']==kind]
        if not rules:return row
        receipts=[json.loads(x[0]) for x in self.db.execute("SELECT payload FROM evidence WHERE channel=? AND kind='reward' AND at_ms BETWEEN ? AND ?",(channel,stamp-120000,stamp))]
        replies=[json.loads(x[0]) for x in self.db.execute("SELECT payload FROM evidence WHERE channel=? AND kind='bot_result' AND at_ms BETWEEN ? AND ?",(channel,stamp-5000,stamp+5000))]
        matches=[]
        for rule in rules:
            if rule.get('mode')=='correlation':
                if kind=='timeout' and row.get('duration')!=rule['duration']:continue
                candidates=[]
                for receipt in receipts:
                    target=reward_target(receipt['input'])
                    if target!=row['user'] or receipt['reward']!=rule['reward'] or stamp-receipt['at_ms']>15000:continue
                    if receipt.get('status')=='canceled' or any(o.get('status')=='canceled' and o['buyer']==receipt['buyer'] and o['reward']==receipt['reward'] and o['input']==receipt['input'] and abs(o['at_ms']-receipt['at_ms'])<=5000 for o in receipts):continue
                    candidates.append(receipt)
                if len(candidates)!=1:continue
                receipt=candidates[0]
                near=self.db.execute("SELECT key FROM events WHERE channel=? AND user=? AND kind=? AND at_ms BETWEEN ? AND ?",(channel,row['user'],kind,receipt['at_ms'],receipt['at_ms']+15000)).fetchall()
                if len(near)!=1 or near[0][0]!=row['key']:continue
                matches.append({'buyer':receipt['buyer'],'self':False,'intended':row['user'],'receipt':receipt['id'],'confirmation':None,'mode':'correlation','title':rule.get('title','')})
                continue
            pattern=re.escape(rule['template']).replace(re.escape('{buyer}'),r'@?(?P<buyer>[A-Za-z0-9_]{1,25})').replace(re.escape('{target}'),r'@?(?P<target>[A-Za-z0-9_]{1,25})')
            for reply in replies:
                if reply['bot']!=rule['bot']:continue
                m=re.fullmatch(pattern,reply['text'],re.I)
                if not m or m['target'].lower()!=row['user']:continue
                buyer=m['buyer'].lower()
                candidates=[]
                for receipt in receipts:
                    canceled=any(other.get('status')=='canceled' and other['buyer']==receipt['buyer'] and other['reward']==receipt['reward'] and other['input']==receipt['input'] and abs(other['at_ms']-receipt['at_ms'])<=5000 for other in receipts)
                    if canceled:continue
                    intended=reward_target(receipt['input'])
                    if not intended or receipt.get('status')=='canceled' or receipt['reward']!=rule['reward'] or receipt['buyer']!=buyer or receipt['at_ms']>reply['at_ms']:continue
                    self_mute=kind=='timeout' and rule.get('self') and row['user']==buyer and intended!=buyer
                    if intended==row['user'] or self_mute:candidates.append((receipt,intended,self_mute))
                # No arbitrary choice between competing purchases or actions.
                if len(candidates)!=1:continue
                near=self.db.execute("SELECT key FROM events WHERE channel=? AND user=? AND kind=? AND at_ms BETWEEN ? AND ?",(channel,row['user'],kind,reply['at_ms']-5000,reply['at_ms']+5000)).fetchall()
                if len(near)!=1 or near[0][0]!=row['key']:continue
                receipt,intended,self_mute=candidates[0]
                matches.append({'buyer':buyer,'self':bool(self_mute),'intended':intended,'receipt':receipt['id'],'confirmation':reply['id']})
        if len(matches)==1:row['reward_attribution']=matches[0]
        return row

    def unmute_rows(self,user='',channel=''):
        rules={(r['channel'],r['reward']):r for r in rules_load(self.data) if r['action']=='untimeout'}
        if not rules:return []
        args=[];where="kind='reward'"
        if channel:where+=' AND channel=?';args.append(channel)
        events=[json.loads(p[0]) for p in self.db.execute('SELECT payload FROM evidence WHERE '+where,args)]
        rows=[];seen=[]
        for receipt in sorted(events,key=lambda e:e['at_ms'],reverse=True):
            target=reward_target(receipt.get('input',''))
            if not target or (user and target!=user) or (receipt['channel'],receipt['reward']) not in rules or receipt.get('status')=='canceled':continue
            duplicate=next((old for old in seen if old['buyer']==receipt['buyer'] and old['reward']==receipt['reward'] and reward_target(old.get('input',''))==target and abs(old['at_ms']-receipt['at_ms'])<=5000),None)
            if duplicate:continue
            seen.append(receipt)
            confirmed=self.db.execute("SELECT key FROM events WHERE kind IN ('untimeout','unban') AND user=? AND channel=? AND at_ms BETWEEN ? AND ? LIMIT 1",(target,receipt['channel'],receipt['at_ms'],receipt['at_ms']+15000)).fetchone()
            rows.append({'key':'reward-unmute|'+str(receipt['id']),'kind':'reward_unmute','user':target,'channel':receipt['channel'],'at_ms':receipt['at_ms'],
                'duration':None,'basis':'reward','sources':'["twitch_reward"]','raw':'','context':[],'message_id':'','message':None,
                'last_at_ms':receipt['at_ms'],'repeat_count':1,'sequence':-receipt['at_ms'],'status':'Снятие мута подтверждено' if confirmed else 'Покупка анмута; снятие мута не подтверждено',
                'active':False,'origin':'Награда канала','executors':[],'reward_attribution':{'buyer':receipt['buyer'],'intended':target,'self':False,'mode':'correlation'},'lifted_by':None,'unmute_purchases':[]})
        return rows

def attribution_blocks(row):
    blocks=[];actors=row.get('executors',[])
    if actors:blocks.append({'text':('Снял наказание: ' if row['kind'] in ('unban','untimeout') else 'Выполнил: ')+', '.join('@'+a for a in actors)+' · подтверждено Twitch','style':'meta'})
    reward=row.get('reward_attribution')
    if reward:
        text=('Получил мут сам от себя: покупка награды для @'+reward['intended']) if reward['self'] else ('@'+reward['buyer']+(' купил снятие мута' if row['kind']=='untimeout' else ' купил мут'))
        suffix=' · сопоставлено по награде, цели, времени и событию; исполнитель не подтверждён' if reward.get('mode')=='correlation' else ' · подтверждено наградой, ботом и событием'
        if row['kind'] in ('untimeout','reward_unmute'):
            blocks.append({'text':'АНМУТ КУПИЛ @'+reward['buyer'],'style':'reward_notice'})
            blocks.append({'text':suffix.lstrip(' ·'),'style':'body'})
        else:blocks.append({'text':text+suffix,'style':'meta'})
    if row.get('lifted_by'):blocks.extend(attribution_blocks(row['lifted_by']))
    for buyer in sorted(set(row.get('unmute_purchases',[]))):
        blocks.append({'text':'АНМУТ КУПИЛ @'+buyer,'style':'reward_notice'})
        blocks.append({'text':'Отдельное событие снятия мута не получено.','style':'body'})
    return blocks
