"""Rebuildable union of local actions and durable shared-ban observations."""
import datetime as dt,json,time
from shared_bans import Store

class SharedModeration:
    def __init__(self,index):
        self.index=index;self.db=index.db;self.store=Store(index.data);self.stamps={};self.next_scan=0
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS shared_events(key TEXT PRIMARY KEY,user TEXT,user_id TEXT,channel TEXT,at_ms INTEGER,context TEXT,fetched_at INTEGER);
          CREATE INDEX IF NOT EXISTS shared_pair ON shared_events(user,channel,at_ms);
          CREATE TABLE IF NOT EXISTS shared_matches(shared_key TEXT PRIMARY KEY,local_key TEXT UNIQUE);
        ''')
        # Keep local source rows intact. Negative sequence IDs distinguish external rows.
        expressions={'key':'s.key','kind':"'ban'",'user':'s.user','channel':'s.channel','at_ms':'s.at_ms',
            'duration':'NULL','basis':"'shared_service'",'sources':"'[\"chatterino_shared\"]'",'raw':"''",'context':'s.context',
            'message_id':"''",'message':'NULL','last_at_ms':'s.at_ms','repeat_count':'1'}
        columns=[r['name'] for r in self.db.execute('PRAGMA table_info(actions)')]
        self.db.execute('DROP VIEW IF EXISTS temp.events')
        self.db.execute('CREATE TEMP VIEW events AS SELECT rowid AS sequence,* FROM actions UNION ALL SELECT -s.rowid AS sequence,'+
            ','.join(expressions.get(c,'NULL')+' AS '+c for c in columns)+
            ' FROM shared_events s WHERE NOT EXISTS(SELECT 1 FROM shared_matches m WHERE m.shared_key=s.key)')
        self.db.commit();self.rematch()
    def sync(self,local_changed=False):
        changed=False
        if time.monotonic()>=self.next_scan:
            self.next_scan=time.monotonic()+1
            paths=list((self.store.folder/'cards').glob('*.json'))+list((self.store.folder/'history').glob('*/*.json'))
            for p in paths:
                try:
                    signature=(p.stat().st_mtime_ns,p.stat().st_size)
                    if self.stamps.get(p)==signature:continue
                    user=p.stem if p.parent.name=='cards' else p.parent.name
                    card=self.store.load(user,path=p)
                    if card is None:continue
                    if p.parent.name=='cards':self.store.preserve(card)
                    for row in card['items']:
                        key='shared|'+card['user_id']+'|'+row['channel_id']+'|'+str(row['since'])
                        context=[{'user':card['user'],'user_id':card['user_id'],'channel':row['channel'],'display_name':card['user'],
                            'id':key+'|'+str(i),'text':m['text'],'automod':m['automod'],'source':'chatterino_shared',
                            'time_utc':dt.datetime.fromtimestamp(m['at'],dt.timezone.utc).isoformat(timespec='milliseconds')}
                            for i,m in enumerate(row['messages']) if m['at']<=row['since']]
                        self.db.execute('''INSERT INTO shared_events VALUES(?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET
                            context=excluded.context,fetched_at=excluded.fetched_at WHERE excluded.fetched_at>=shared_events.fetched_at''',
                            (key,card['user'],card['user_id'],row['channel'],row['since']*1000,json.dumps(context,ensure_ascii=False),card['fetched_at']))
                        changed=True
                    self.stamps[p]=signature
                except OSError:continue
            self.db.commit()
        if changed or local_changed:self.rematch()
        return changed
    def rematch(self):
        pairs=[]
        for s in self.db.execute('SELECT * FROM shared_events'):
            identity=self.db.execute('SELECT user_id FROM identities WHERE user=?',(s['user'],)).fetchone()
            if identity and identity[0]!=s['user_id']:continue
            candidates=self.db.execute('''SELECT key,at_ms FROM actions WHERE kind='ban' AND user=? AND channel=?
                AND at_ms BETWEEN ? AND ?''',(s['user'],s['channel'],s['at_ms']-1500,s['at_ms']+1500)).fetchall()
            if len(candidates)!=1:continue
            candidate=candidates[0];low,high=sorted((s['at_ms'],candidate['at_ms']))
            if self.db.execute('''SELECT 1 FROM actions WHERE user=? AND channel=? AND kind IN ('unban','untimeout','speech','timeout','ban')
                AND at_ms>=? AND at_ms<=? AND key<>? LIMIT 1''',(s['user'],s['channel'],low,high,candidate['key'])).fetchone():continue
            pairs.append((s['key'],candidate['key']))
        counts={}
        for _,local in pairs:counts[local]=counts.get(local,0)+1
        with self.db:
            self.db.execute('DELETE FROM shared_matches')
            self.db.executemany('INSERT INTO shared_matches VALUES(?,?)',[(s,l) for s,l in pairs if counts[l]==1])
    def enrich(self,row):
        if row['key'].startswith('shared|'):
            row['origin']='Общие баны Chatterino+';row['shared_only']=True
            found=self.db.execute('SELECT * FROM shared_events WHERE key=?',(row['key'],)).fetchone()
        else:
            row['origin']='Наш архив';row['shared_only']=False
            found=self.db.execute('''SELECT s.* FROM shared_events s JOIN shared_matches m ON m.shared_key=s.key
                WHERE m.local_key=?''',(row['key'],)).fetchone()
            if found:
                row['origin']='Наш архив + Chatterino+'
                existing={(m.get('time_utc'),m.get('text')):m for m in row['context']}
                for message in json.loads(found['context']):
                    if not message.get('user_id'):message['user_id']=found['user_id']
                    # Shared timestamps have second precision; prefer the archived message ID.
                    match=next((m for m in existing.values() if m['text']==message['text'] and
                        abs(dt.datetime.fromisoformat(m['time_utc'].replace('Z','+00:00')).timestamp()-
                            dt.datetime.fromisoformat(message['time_utc']).timestamp())<1),None)
                    if match is not None:match['automod']=match.get('automod',False) or message.get('automod',False)
                    else:existing[(message['time_utc'],message['text'])]=message
                row['context']=sorted(existing.values(),key=lambda m:m['time_utc'])
        if found:
            for message in row['context']:
                if not message.get('user_id'):message['user_id']=found['user_id']
        row['shared_fetched_at']=found['fetched_at'] if found else None
        return row
