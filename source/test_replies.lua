local path,dir=assert(arg[1]),assert(arg[2]);package.path=path..'/?.lua;'..package.path
local core=require('history_core');local Mod=require('moderation_core');local realopen=io.open
local function open(p,m)return realopen(dir..'/'..p,m)end
local function eq(a,b)assert(a==b,tostring(a)..' ~= '..tostring(b))end
local n=0;local function test(label,fn)fn();n=n+1;print('PASS '..label)end
c2={MessageElementFlag={RepliedMessage=1<<32},MessageFlag={ReplyMessage=1<<24,System=1,Timeout=2}}
local E=c2.MessageElementFlag.RepliedMessage
local msg={id='reply-1',login_name='someone',channel_name='channel',display_name='Someone',
    message_text='My answer',server_received_time=1788868800000,flags=c2.MessageFlag.ReplyMessage}
function msg:elements()return {
    {type='text',flags=E,words={'Ответ','для'},link={value='thread-root-id'}},
    {type='text',flags=E,words={'@Other:'},link={value='Other'}},
    {type='single-line-text',flags=E|2,words={'Quoted','','текст','"original"'},link={value='thread-root-id'}},
    {type='text',flags=2,words={'Wrong body'}}}end
test('direct quote text and author come only from reply elements',function()
 local r=core.reply(msg,'channel');eq(r.user,'other');eq(r.text,'Quoted  текст "original"');eq(r.thread_id,'thread-root-id')
 eq(r.id,nil);eq(r.state,'available')
end)
test('quote is retained even without ReplyMessage flag or parent in the buffer',function()
 msg.flags=0;eq(core.reply(msg,'channel').state,'available')
end)
test('ordinary mention is not a reply and missing data is explicit',function()
 eq(core.reply({message_text='@Other hi',flags=0},'channel'),nil)
 eq(core.reply({flags=c2.MessageFlag.ReplyMessage},'channel').state,'unavailable')
end)
test('legacy message is enriched once without raising message count',function()
 local f=assert(open('someone--channel.jsonl','wb'));f:write('{"id":"reply-1","text":"old record"}\n');f:close()
 local a=core.archive(open,{user='someone',channel='channel'});a.enabled=true;eq(a.count,1)
 eq(a:record(msg,'buffer'),true);eq(a.count,1);eq(a:record(msg,'buffer'),false)
 a=core.archive(open,{user='someone',channel='channel'});a.enabled=true;eq(a:record(msg,'buffer'),false);eq(a.count,1)
end)
test('moderation context retains the reply after original message object is gone',function()
 local m=Mod.new(open,c2.MessageFlag,function()return 1788868801000 end)
 m:process(msg,'channel','live')
 m:process({flags=3,message_text='someone забанен.',server_received_time=1788868801000},'channel','live')
 local f=assert(open('moderation.jsonl','rb'));local text=f:read('a');f:close()
 assert(text:find('"reply":{"state":"available"',1,true));assert(text:find('"user":"other"',1,true))
end)
print(n..' reply tests passed')
