import re


def repeat_count(raw):
    text=str(raw or '')
    match=re.search(r'\(\s*(?:[×xх]\s*(\d+)|(\d+)\s*(?:раз(?:а)?|times?))\s*\)\s*$',text,re.I)
    if match:return max(1,min(100000,int(match.group(1) or match.group(2))))
    return 2 if re.search(r'\(multiple times\)\s*$',text,re.I) else 1


def aggregate_target(store,e,key,user,channel,kind,stamp):
    if kind not in ('ban','timeout'):return None
    replaces=e.get('replaces_key','')
    candidates=[]
    if isinstance(replaces,str) and replaces and replaces!=key:
        alias=store.db.execute('SELECT canonical FROM aliases WHERE key=?',(replaces,)).fetchone()
        target=store.db.execute('SELECT * FROM actions WHERE key=?',(alias[0] if alias else replaces,)).fetchone()
        if target:candidates.append(target)
    count=repeat_count(e.get('raw'))
    if not candidates and count>1:
        candidates=store.db.execute('''SELECT * FROM actions WHERE user=? AND channel=? AND kind=?
            AND COALESCE(last_at_ms,at_ms)<=? AND COALESCE(last_at_ms,at_ms)>=? AND key<>?
            ORDER BY COALESCE(last_at_ms,at_ms) DESC''',(user,channel,kind,stamp,stamp-15000,key)).fetchall()
        candidates=[c for c in candidates if count>c['repeat_count'] or
                    (count==c['repeat_count'] and stamp-(c['last_at_ms'] or c['at_ms'])<=250
                     and e.get('raw','').strip()==c['raw'].strip())]
    for candidate in candidates:
        if (candidate['user'],candidate['channel'],candidate['kind'])!=(user,channel,kind):continue
        if stamp<candidate['at_ms']:continue
        other=store.db.execute('''SELECT 1 FROM actions WHERE user=? AND channel=?
            AND kind IN ('ban','timeout','unban','untimeout','speech')
            AND at_ms>=? AND at_ms<=? AND key<>? LIMIT 1''',
            (user,channel,candidate['at_ms'],stamp,candidate['key'])).fetchone()
        if not other:return candidate
    return None
