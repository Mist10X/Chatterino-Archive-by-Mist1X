local path,dir=assert(arg[1]),assert(arg[2]);package.path=path..'/?.lua;'..package.path
local M=require('moderation_core');local realopen=io.open
local function open(p,m) return realopen(dir..'/'..p,m) end
local function eq(x,y) assert(x==y,tostring(x)..' ~= '..tostring(y)) end
local n=0;local function test(label,fn) fn();n=n+1;print('PASS '..label) end
local F={System=1,Timeout=2,Untimeout=1024,RecentMessage=32768,SharedMessage=2^37,EventSub=2^40}
local now=1788868800000
local a=M.new(open,F,function() return now end)
local function msg(i,channel,user,at)
 return {id='id-'..i,login_name=user or 'someone',channel_name=channel or 'one',message_text='Message '..i,
 display_name='Someone',user_id='123',server_received_time=at or now-20000+i*1000,flags=0}
end
local function action(kind,at,user)
 return {flags=F.System | (kind=='unban' and F.Untimeout or F.Timeout),server_received_time=at or now,
 message_text=(user or 'someone')..(kind=='ban' and ' has been permanently banned.' or
 kind=='unban' and ' was unbanned by mod.' or ' has been timed out for 10m.')}
end
local function records()
 local f=assert(open('moderation.jsonl','rb'));local text=f:read('a');f:close();return text
end
test('ignore ordinary text that claims someone is banned',function()
 local m=action('ban');m.flags=0;eq(M.parse(m,F,'one','live',now),nil)
end)
test('parse duration and ignore unknown flagged formats',function()
 eq(M.repeat_count('someone забанен. (х2)'),2)
 eq(M.repeat_count('someone забанен. (×3)'),3)
 eq(M.repeat_count('someone banned. (2 times)'),2)
 eq(M.duration('someone has been timed out for 1h 2m 3s.'),3723)
 eq(M.duration('someone has been timed out for 60 seconds.'),60)
 eq(M.duration('someone has been timed out.'),nil)
 eq(M.duration('someone получил мут на 14д.'),1209600)
 eq(M.duration('someone получил мут на 10м.'),600)
 local ru=action('timeout');ru.message_text='someone получил мут на 30с.'
 local parsed=M.parse(ru,F,'one','live',now);eq(parsed.kind,'timeout');eq(parsed.duration,30)
 local m=action('ban');m.message_text='unrecognized translated text';local e,reason=M.parse(m,F,'one','live',now)
 eq(e,nil);eq(reason,'unrecognized')
end)
test('save last 10 messages for a ban and last 5 for a timeout',function()
 for i=1,12 do a:process(msg(i),'one','live');a:process(msg(i,'two'),'two','live') end
 a:process(action('ban'),'one','live')
 a:process(action('timeout',now+1),'two','live')
 local text=records();local ban=text:match('(%{"kind":"ban"[^\n]+)')
 local timeout=text:match('(%{"kind":"timeout"[^\n]+)')
 local _,bn=ban:gsub('"time_utc"','');eq(bn,10)
 local _,tn=timeout:gsub('"time_utc"','');eq(tn,5)
 assert(ban:find('"id":"id-3"',1,true));assert(not ban:find('"id":"id-2"',1,true))
 assert(timeout:find('"id":"id-8"',1,true))
end)
test('replayed action does not append duplicate',function()
 local before=records();a:process(action('ban'),'one','buffer');eq(records(),before)
end)
test('old, historical and other-channel messages do not clear a ban',function()
 a:process(msg(99,'one','someone',now-1),'one','live');assert(a.active['one/someone'])
 local historical=msg(100,'one','someone',now+1);historical.flags=F.RecentMessage
 a:process(historical,'one','live');assert(a.active['one/someone'])
 a:process(msg(101,'elsewhere','someone',now+2),'elsewhere','live');assert(a.active['one/someone'])
end)
test('new live message resolves only same-channel punishment',function()
 a:process(msg(102,'one','someone',now+3),'one','live');eq(a.active['one/someone'],nil)
 assert(a.active['two/someone']);assert(records():find('"kind":"speech"',1,true))
end)
test('reban and explicit unban preserve history',function()
 a:process(action('ban',now+4),'one','live');assert(a.active['one/someone'])
 a:process(action('unban',now+5),'one','live');eq(a.active['one/someone'],nil)
 local _,bans=records():gsub('"kind":"ban"','');eq(bans,2)
end)
test('restart restores outstanding punishment and deduplicates history',function()
 a=M.new(open,F,function()return now end);assert(a.active['two/someone']);eq(a.active['one/someone'],nil)
 local before=records();a:process(action('timeout',now+1),'two','buffer');eq(records(),before)
 a:process(msg(103,'two','someone',now+10),'two','live');eq(a.active['two/someone'],nil)
end)
test('failed disk write does not mark action saved',function()
 local bad=M.new(function(p,m) if m=='rb' then return nil,'missing',2 end return nil,'disk full',28 end,F,function()return now end)
 local ok=pcall(function()bad:process(action('ban'),'one','live')end)
 eq(ok,false);eq(next(bad.active),nil)
end)
test('delayed old ban cannot resurrect a resolved punishment after restart',function()
 a:process(action('ban',now+100,'delayed'),'one','live')
 a:process(action('unban',now+100,'delayed'),'one','live')
 a:process(action('ban',now+90,'delayed'),'one','buffer')
 eq(a.active['one/delayed'],nil)
 a=M.new(open,F,function()return now end);eq(a.active['one/delayed'],nil)
end)
test('replacement links survive restart without duplicate writes',function()
 local function isolated(p,m) return realopen(dir..'/replacement-'..p,m) end
 local archive=M.new(isolated,F,function()return now end)
 for i,kind in ipairs({'ban','timeout'}) do
  local first=action(kind,now+i*1000)
  local second=action(kind,now+i*1000+100);second.message_text=second.message_text..' (×2)'
  archive:process(first,'one','live');archive:process(second,'one','replace',nil,first)
  local f=assert(isolated('moderation.jsonl','rb'));local before=f:read('a');f:close()
  assert(before:find('"replaces_key":"one|someone|'..kind,1,true))
  archive=M.new(isolated,F,function()return now end)
  archive:process(second,'one','replace',nil,first)
  f=assert(isolated('moderation.jsonl','rb'));eq(f:read('a'),before);f:close()
 end
end)
print(n..' moderation tests passed')
