local plugin, directory = assert(arg[1]), assert(arg[2])
package.path = plugin .. '/?.lua;' .. package.path
local core = require('history_core')
local protocol = require('panel_protocol')
local realopen = io.open
local function open(path, mode) return realopen(directory .. '/' .. path, mode) end
local function put(path, text) local f=assert(open(path,'wb')); assert(f:write(text)); f:close() end
local function get(path) local f=open(path,'rb'); if not f then return '' end; local t=f:read('a');f:close();return t end
local function eq(a,b) assert(a==b, tostring(a)..' ~= '..tostring(b)) end
local tests = 0
local function test(label, fn) fn(); tests=tests+1; print('PASS '..label) end
local function msg(id, user, channel)
    return {id=id, login_name=user or 'Mist1X_X', channel_name=channel or 'morphe_ya',
            display_name=user or 'Mist1X_X', message_text='Test '..id, server_received_time=1788868800000}
end
local function channel(name)
    local ch={items={},callbacks={},valid=true,cleared=0,name=name}
    function ch:is_valid() return self.valid end
    function ch:get_type() return 'Twitch' end
    function ch:get_name() return self.name end
    function ch:count_messages() return #self.items end
    function ch:last_message() return self.items[#self.items] end
    function ch:message_snapshot(count)
        local list={}; for i=math.max(1,#self.items-count+1),#self.items do list[#list+1]=self.items[i] end; return list
    end
    function ch:clear_messages() self.items={};self.cleared=self.cleared+1 end
    function ch:add_message(message, context)
        self.items[#self.items+1]=message
        for _,fn in pairs(self.callbacks) do fn(message) end
    end
    function ch:on_message_appended(fn)
        local key={};self.callbacks[key]=fn
        return {disconnect=function() self.callbacks[key]=nil end}
    end
    function ch:add_system_message(_) end
    return ch
end
local channels={morphe_ya=channel('morphe_ya'), dangerlyoha=channel('dangerlyoha')}
local timers,commands={},{}
local function config(rev, user1, user2)
    put('panel-control.txt', 'HT2\nrevision\t'..rev..'\nkeep\t1000\nuser\tmist1x_x\t'..user1
        ..'\nuser\tother_user\t'..user2..'\nchannel\tmorphe_ya\nchannel\tdangerlyoha\nEND\n')
end
local function runtime()
    commands={};timers={}
    c2={Channel={by_name=function(n) return channels[n] end},ChannelType={Twitch='Twitch'},
        MessageContext={Repost='repost'},LogLevel={Info=1,Warning=2},log=function() end,
        register_command=function(n,fn) commands[n]=fn;return true end,
        later=function(fn,ms) eq(ms,2000);timers[#timers+1]=fn end}
    io.open=open
    dofile(plugin .. '/init.lua')
end
local function tick() timers[#timers]() end
local function count(user, channel)
    local a=core.archive(open,{user=user,channel=channel});return a.count
end

test('reject malformed or executable configuration',function()
    assert(not pcall(protocol.parse, 'HT2\nrevision\tx\nEND'))
    assert(not pcall(protocol.parse, 'HT2\nrevision\tx\nload("bad")\nEND\n'))
    assert(not pcall(protocol.parse, 'HT2\nrevision\tx\nuser\t../bad\ton\nEND\n'))
    eq(protocol.parse('HT2\nrevision\tx\nuser\tu\toff\nEND\n').users.u,false)
end)
test('record one user globally into separate channel archives',function()
    config('v1','on','off')
    channels.morphe_ya:add_message(msg('buffer-1'))
    runtime()
    channels.morphe_ya:add_message(msg('live-1'))
    channels.dangerlyoha:add_message(msg('live-2','Mist1X_X','dangerlyoha'))
    channels.morphe_ya:add_message(msg('ignored','random_user'))
    eq(count('mist1x_x','morphe_ya'),2)
    eq(count('mist1x_x','dangerlyoha'),1)
    eq(count('random_user','morphe_ya'),0)
end)
test('pause live recording and resume with available buffer, without duplicates',function()
    config('v2','off','on');tick()
    channels.morphe_ya:add_message(msg('paused'))
    channels.morphe_ya:add_message(msg('other','other_user'))
    eq(count('mist1x_x','morphe_ya'),2)
    eq(count('other_user','morphe_ya'),1)
    config('v3','on','on');tick()
    eq(count('mist1x_x','morphe_ya'),3)
    tick();tick();eq(count('mist1x_x','morphe_ya'),3)
end)
test('incomplete control write preserves last valid settings and exposes error',function()
    put('panel-control.txt','HT2\nrevision\tbroken\n');tick()
    assert(get('panel-status.json'):find('Incomplete configuration',1,true))
    channels.morphe_ya:add_message(msg('during-error'))
    eq(count('mist1x_x','morphe_ya'),4)
    config('v4','on','on');tick()
end)
test('reopen channel attaches once and captures buffer',function()
    local old=channels.dangerlyoha;old.valid=false
    channels.dangerlyoha=channel('dangerlyoha')
    channels.dangerlyoha:add_message(msg('reopened','Mist1X_X','dangerlyoha'))
    tick();eq(next(old.callbacks),nil)
    eq(count('mist1x_x','dangerlyoha'),2)
    tick();local n=0;for _ in pairs(channels.dangerlyoha.callbacks) do n=n+1 end;eq(n,1)
end)
test('trim button retains last 1000 and does not change archived messages',function()
    for i=1,1105 do channels.morphe_ya:add_message(msg('bulk-'..i,'not_tracked')) end
    local original_archive=get('mist1x_x--morphe_ya.jsonl')
    put('panel-request.txt','HT2\ntrim\trequest1\tmorphe_ya\t1000\nEND\n');tick()
    eq(channels.morphe_ya:count_messages(),1000)
    eq(channels.morphe_ya.items[1].id,'bulk-106')
    eq(get('mist1x_x--morphe_ya.jsonl'),original_archive)
    assert(get('panel-ack.json'):find('"status":"ok"',1,true))
    local cleared=channels.morphe_ya.cleared;tick();eq(channels.morphe_ya.cleared,cleared)
end)
test('restart preserves recording, deduplicates and never replays accepted trim',function()
    for _,ch in pairs(channels) do ch.callbacks={} end
    local cleared=channels.morphe_ya.cleared
    runtime();eq(channels.morphe_ya.cleared,cleared)
    eq(count('mist1x_x','dangerlyoha'),2)
    channels.morphe_ya:add_message(msg('after-restart'))
    eq(count('mist1x_x','morphe_ya'),5)
end)
test('closed channel trim fails visibly without clearing other channels',function()
    local cleared=channels.morphe_ya.cleared
    put('panel-request.txt','HT2\ntrim\trequest2\tclosed_channel\t1000\nEND\n');tick()
    assert(get('panel-ack.json'):find('"status":"error"',1,true))
    eq(channels.morphe_ya.cleared,cleared)
end)
test('discover a new open tab even while panel is closed',function()
    channels.fresh_channel=channel('fresh_channel')
    c2.windows={all=function() return {{notebook={page_count=1,page_at=function()
        return {splits=function() return {{channel=channels.fresh_channel}} end}
    end}}} end}
    tick()
    channels.fresh_channel:add_message(msg('automatic','Mist1X_X','fresh_channel'))
    eq(count('mist1x_x','fresh_channel'),1)
    assert(get('panel-status.json'):find('"discovery":"open_tabs"',1,true))
end)
local function all_open(names)
    c2.windows={all=function() return {{notebook={page_count=2,page_at=function()
        return {splits=function() local splits={};for _,name in ipairs(names) do splits[#splits+1]={channel=channels[name]} end;return splits end}
    end}}} end}
end
local function batch(id,excluded)
    local text='HT2\ntrim_except\t'..id..'\t1000\nchannel\tdangerlyoha\nchannel\tfresh_channel\nchannel\tmorphe_ya\n'
    for _,name in ipairs(excluded or {}) do text=text..'exclude\t'..name..'\n' end
    put('panel-request.txt',text..'END\n')
end
test('strict batch protocol rejects malformed, duplicate and all-excluded requests',function()
    for _,body in ipairs({'channel\tone\nexclude\ttwo\n','channel\tone\nchannel\tone\n','channel\tone\nexclude\tone\n','channel\t../bad\n','channel\tone\nBAD\n','channel\tone'}) do
        eq(protocol.request('HT2\ntrim_except\tid\t1000\n'..body..'END\n'),nil)
    end
    eq(protocol.request('HT2\ntrim_except\tid\t999\nchannel\tone\nEND\n'),nil)
end)
test('batch excludes protected channel across duplicate tabs and preserves archives',function()
    all_open({'dangerlyoha','fresh_channel','morphe_ya'})
    for name,ch in pairs(channels) do for i=1,1010 do ch:add_message(msg('except-'..i,'not_tracked',name)) end end
    local original=get('mist1x_x--morphe_ya.jsonl');local protected=#channels.morphe_ya.items
    local cleared=channels.dangerlyoha.cleared
    batch('batch1',{'morphe_ya'});tick()
    eq(#channels.morphe_ya.items,protected);eq(#channels.dangerlyoha.items,1000);eq(#channels.fresh_channel.items,1000)
    eq(channels.dangerlyoha.cleared,cleared+1);eq(get('mist1x_x--morphe_ya.jsonl'),original)
    assert(get('panel-ack.json'):find('2/2',1,true));assert(get('panel-ack.json'):find('"status":"ok"',1,true))
    tick();eq(channels.dangerlyoha.cleared,cleared+1)
end)
test('changed open tabs reject the entire batch before clearing',function()
    all_open({'morphe_ya','dangerlyoha'});local before=channels.morphe_ya.cleared
    batch('batch-changed',{});tick();eq(channels.morphe_ya.cleared,before)
    assert(get('panel-ack.json'):find('"status":"error"',1,true))
end)
test('empty exclusions trim every open channel',function()
    all_open({'dangerlyoha','fresh_channel','morphe_ya'});batch('batch-all',{});tick()
    for _,ch in pairs(channels) do eq(#ch.items,1000) end
    assert(get('panel-ack.json'):find('3/3',1,true))
end)
test('partial failure reports each channel while protecting exclusions',function()
    for _,name in ipairs({'dangerlyoha','fresh_channel'}) do channels[name]:add_message(msg('extra','not_tracked',name)) end
    local old=channels.dangerlyoha.message_snapshot
    channels.dangerlyoha.message_snapshot=function() error('Test snapshot unavailable') end
    local protected=#channels.morphe_ya.items
    batch('batch-failure',{'morphe_ya'});tick()
    eq(#channels.dangerlyoha.items,1001);eq(#channels.fresh_channel.items,1000);eq(#channels.morphe_ya.items,protected)
    assert(get('panel-ack.json'):find('"status":"error"',1,true));assert(get('panel-ack.json'):find('1/2',1,true))
    channels.dangerlyoha.message_snapshot=old
end)
test('restart cancels interrupted batch without replaying it',function()
    batch('interrupted',{});put('panel-ack.json','{"id":"interrupted","status":"processing"}')
    for _,ch in pairs(channels) do ch.callbacks={} end
    local before=channels.dangerlyoha.cleared;runtime();eq(channels.dangerlyoha.cleared,before)
    assert(get('panel-ack.json'):find('"status":"error"',1,true))
end)

test('excluded open channel detaches and remains available for trim discovery',function()
    all_open({'dangerlyoha','morphe_ya'})
    local before=count('mist1x_x','morphe_ya')
    put('panel-control.txt','HT2\nrevision\texcluded\nkeep\t1000\nuser\tmist1x_x\ton\nchannel\tdangerlyoha\nexclude\tmorphe_ya\nEND\n')
    tick();tick()
    channels.morphe_ya:add_message(msg('must-not-record'))
    eq(count('mist1x_x','morphe_ya'),before)
    assert(get('panel-status.json'):find('"name":"morphe_ya","attached":false,"open":true',1,true))
    eq(next(channels.morphe_ya.callbacks),nil)
end)

io.open=realopen
print(tests..' integration tests passed')
