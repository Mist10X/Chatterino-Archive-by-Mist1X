"""Overlay saved AutoMod context onto a replay ban; never insert a public message."""
import json,sqlite3
from contextlib import closing

def enrich_bans(data,rows):
    bans=[r for r in rows if r.get('kind')=='ban' and r.get('user')]
    path=data/'panel-cache/moderation.sqlite3'
    if not bans or not path.exists():return rows
    enriched={}
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=1)) as db:
            for row in bans:
                args=(row['user'],row['channel'],row['at']-1500,row['at']+1500)
                found=db.execute('SELECT at_ms,context,user_id FROM shared_events WHERE user=? AND channel=? AND at_ms BETWEEN ? AND ?',args).fetchall()
                local=db.execute("SELECT count(*) FROM actions WHERE kind='ban' AND user=? AND channel=? AND at_ms BETWEEN ? AND ?",args).fetchone()[0]
                if len(found)!=1 or local>1:continue
                since,context,uid=found[0];low,high=sorted((since,row['at']))
                identity=db.execute('SELECT user_id FROM identities WHERE user=?',(row['user'],)).fetchone()
                if identity and identity[0] and str(identity[0])!=str(uid):continue
                if db.execute("SELECT 1 FROM actions WHERE user=? AND channel=? AND kind IN ('unban','untimeout','speech','timeout') AND at_ms BETWEEN ? AND ? LIMIT 1",(row['user'],row['channel'],low,high)).fetchone():continue
                messages=json.loads(context)
                if messages:enriched[row['seq']]={**row,'ban_context':messages}
    except (OSError,sqlite3.Error,ValueError):return rows
    return [enriched.get(row['seq'],row) for row in rows]
