-- Archive only structured moderation messages, never commands or ordinary chat text.
local core = require('history_core')
local M = {}
local function valid(s) return type(s)=='string' and #s<=64 and s:match('^[a-zA-Z0-9_]+$') end
local function has(msg, flags, key)
    local value = tonumber(core.field(msg,'flags',0)) or 0
    local flag = tonumber(flags[key]) or 0
    return flag ~= 0 and (value & flag) ~= 0
end
M.has = has

function M.repeat_count(text)
    return tonumber(text:match('%(×(%d+)%)') or text:match('%(x(%d+)%)') or text:match('%(х(%d+)%)')
        or text:match('%((%d+) раз') or text:match('%((%d+) times?%)')) or 1
end

function M.duration(text)
    text=text:gsub('получил мут на ','has been timed out for ')
        :gsub('(%d+)д','%1d'):gsub('(%d+)ч','%1h'):gsub('(%d+)м','%1m'):gsub('(%d+)с','%1s')
    local direct = text:match('for (%d+) seconds?') or text:match('на (%d+) сек')
    if direct then return tonumber(direct) end
    local fragment = text:match('for ([%d%a%s]+)%.') or text:match('for ([%d%a%s]+)$')
    if not fragment then return nil end
    local sum, matched = 0, false
    local units={s=1,sec=1,secs=1,second=1,seconds=1,m=60,min=60,mins=60,minute=60,minutes=60,
        h=3600,hour=3600,hours=3600,d=86400,day=86400,days=86400,w=604800,week=604800,weeks=604800}
    for count, unit in fragment:gmatch('(%d+)%s*([a-z]+)') do
        if not units[unit] then return nil end
        sum=sum+tonumber(count)*units[unit];matched=true
    end
    return matched and sum or nil
end

function M.parse(msg, flags, channel, source, now)
    local timeout, untimeout = has(msg,flags,'Timeout'), has(msg,flags,'Untimeout')
    if not timeout and not untimeout then return nil end
    if has(msg,flags,'ClearChat') then return nil end
    local text=tostring(core.field(msg,'message_text',''))
    local lower=text:lower()
    local user, kind
    if untimeout then
        user=lower:match('^([a-z0-9_]+) was unbanned') or lower:match('^([a-z0-9_]+) has been unbanned')
            or lower:match('^[a-z0-9_]+ unbanned ([a-z0-9_]+)')
            or lower:match('^([a-z0-9_]+) разбанен')
        if user then kind='unban' end
        if not user then
            user=lower:match('^([a-z0-9_]+) was untimed out') or lower:match('^[a-z0-9_]+ untimed out ([a-z0-9_]+)')
                or lower:match('^([a-z0-9_]+) has been untimed out') or lower:match('^([a-z0-9_]+) размучен')
            if user then kind='untimeout' end
        end
    else
        user=lower:match('^([a-z0-9_]+) has been permanently banned') or lower:match('^([a-z0-9_]+) has been banned')
            or lower:match('^[a-z0-9_]+ permanently banned ([a-z0-9_]+)') or lower:match('^[a-z0-9_]+ banned ([a-z0-9_]+)')
            or lower:match('^([a-z0-9_]+) забанен') or lower:match('^([a-z0-9_]+) заблокирован навсегда')
        if user then kind='ban' end
        if not user then
            user=lower:match('^([a-z0-9_]+) has been timed out') or lower:match('^[a-z0-9_]+ timed out ([a-z0-9_]+)')
                or lower:match('^([a-z0-9_]+) замучен') or lower:match('^([a-z0-9_]+) получил таймаут')
                or lower:match('^([a-z0-9_]+) получил мут')
            if user then kind='timeout' end
        end
    end
    if not valid(user) then return nil, 'unrecognized' end
    local stamp=tonumber(core.field(msg,'server_received_time',0)) or 0
    local basis='server'
    if stamp<=0 then
        if source=='buffer' or has(msg,flags,'RecentMessage') then return nil,'missing_time' end
        stamp=tonumber(core.field(msg,'parse_time',0)) or 0
        if stamp<=0 then stamp=now end
        if stamp<=0 then return nil,'missing_time' end
        basis='observed'
    end
    local event={kind=kind,user=user,channel=core.name(channel),at_ms=math.floor(stamp),time_basis=basis,
        duration=kind=='timeout' and M.duration(lower) or nil,raw=text,
        source=has(msg,flags,'EventSub') and 'eventsub' or (has(msg,flags,'PubSub') and 'pubsub' or 'chatterino'),
        message_id=tostring(core.field(msg,'id',''))}
    -- Timestamp and action identify a logical record independently of a wrapper object or replay.
    event.key=table.concat({event.channel,event.user,event.kind,tostring(event.at_ms),tostring(event.duration or '')},'|')
    return event
end

function M.encode(e)
    return '{"kind":'..core.quote(e.kind)..',"user":'..core.quote(e.user)..',"channel":'..core.quote(e.channel)
        ..',"at_ms":'..string.format('%.0f',e.at_ms)..',"key":'..core.quote(e.key or '')
        ..',"duration":'..(e.duration and tostring(e.duration) or 'null')
        ..',"time_basis":'..core.quote(e.time_basis or 'server')..',"source":'..core.quote(e.source or '')
        ..',"message_id":'..core.quote(e.message_id or '')..',"user_id":'..core.quote(e.user_id or '')
        ..',"replaces_key":'..core.quote(e.replaces_key or '')..',"report_key":'..core.quote(e.report_key or e.key or '')
        ..',"raw":'..core.quote(e.raw or '')..',"context":['..table.concat(e.context or {},',')..']}'
end

function M.new(open, flags, now)
    local a={open=open,flags=flags,now=now,seen={},active={},latest={},cache={},identities={},unparsed=0,count=0}
    function a:remember(e)
        local pair=e.channel..'/'..e.user
        if e.at_ms<(self.latest[pair] or 0) then return end
        self.latest[pair]=e.at_ms
        if e.kind=='ban' or e.kind=='timeout' then self.active[pair]=e
        else self.active[pair]=nil end
    end
    local saved,err,code=open('moderation.jsonl','rb')
    if not saved and code~=2 then error('Cannot read moderation archive: '..tostring(err)) end
    if saved then
        for line in saved:lines() do
            if line:sub(-1)=='}' then
                local kind,user,channel,at=line:match('^%{"kind":"([a-z_]+)","user":"([a-z0-9_]+)","channel":"([a-z0-9_]+)","at_ms":(%d+)')
                local key=line:match(',"report_key":"([^"\\]+)"') or line:match(',"key":"([^"\\]+)"')
                if key then a.seen[key]=true end
                if kind and user then
                    if kind=='identity' then a.identities[user]=line:match(',"user_id":"(%d+)"')
                    else a:remember({kind=kind,user=user,channel=channel,at_ms=tonumber(at)}) end
                end
            end
        end
        saved:close()
    end
    function a:save(e)
        local report=e.report_key or e.key
        if self.seen[report] then return false end
        core.append(self.open,'moderation.jsonl','\n'..M.encode(e)..'\n')
        self.seen[report]=true
        self.count=self.count+1
        return true
    end
    function a:process(msg,channel,source,snapshot,previous)
        local e,reason=M.parse(msg,self.flags,channel,source,self.now())
        if reason then self.unparsed=self.unparsed+1;return end
        if e then
            if previous and (e.kind=='ban' or e.kind=='timeout') then
                local old=M.parse(previous,self.flags,channel,'buffer',self.now())
                if old and old.kind==e.kind and old.user==e.user and old.channel==e.channel then
                    e.replaces_key=old.key
                    e.report_key=e.key..'|replace|'..old.key..'|'..tostring(M.repeat_count(e.raw))
                end
            end
            if self.seen[e.report_key or e.key] then return end
            local pair=e.channel..'/'..e.user
            if e.kind=='ban' or e.kind=='timeout' then
                local need=e.kind=='ban' and 10 or 5
                local list=self.cache[pair] or {}
                local records, seen={},{}
                local function collect(items)
                    for i=#items,1,-1 do
                        local m=items[i]
                        local stamp=tonumber(core.field(m,'server_received_time',0)) or 0
                        local user=tostring(core.field(m,'login_name','')):lower()
                        local id=tostring(core.field(m,'id',''))
                        if user==e.user and id~='' and not seen[id] and stamp>0 and stamp<=e.at_ms
                            and not has(m,self.flags,'System') and not has(m,self.flags,'SharedMessage') then
                            seen[id]=true;records[#records+1]=m
                            if #records>=need then break end
                        end
                    end
                end
                collect(list)
                if #records<need and snapshot then collect(snapshot()) end
                table.sort(records,function(x,y) return core.field(x,'server_received_time',0)<core.field(y,'server_received_time',0) end)
                e.context={}
                for _,m in ipairs(records) do e.context[#e.context+1]=core.encode_message(m,e.channel,'moderation_context') end
                if self:save(e) then self:remember(e) end
            else
                if self:save(e) then self:remember(e) end
            end
            return
        end
        if has(msg,self.flags,'System') or has(msg,self.flags,'SharedMessage') then return end
        local user=tostring(core.field(msg,'login_name','')):lower()
        local id=tostring(core.field(msg,'id',''))
        if not valid(user) or id=='' then return end
        local stamp=tonumber(core.field(msg,'server_received_time',0)) or 0
        local uid=tostring(core.field(msg,'user_id',''))
        if uid:match('^%d+$') and self.identities[user]~=uid then
            self:save({kind='identity',user=user,channel=channel,at_ms=math.max(0,stamp),key='identity|'..user..'|'..uid,user_id=uid})
            self.identities[user]=uid
        end
        local pair=channel..'/'..user
        local cache=self.cache[pair] or {}
        if #cache==0 or core.field(cache[#cache],'id','')~=id then
            -- Store only strings and timestamps, never references to Chatterino's message layouts.
            cache[#cache+1]={id=id,login_name=user,display_name=core.field(msg,'display_name',user),user_id=uid,
                channel_name=channel,server_received_time=stamp,message_text=core.field(msg,'message_text',''),
                archive_reply=core.reply(msg,channel)}
            if #cache>10 then table.remove(cache,1) end
        end
        self.cache[pair]=cache
        local active=self.active[pair]
        if active and stamp>active.at_ms and not has(msg,self.flags,'RecentMessage') and source=='live' then
            local evidence={kind='speech',user=user,channel=channel,at_ms=stamp,key='speech|'..channel..'|'..id,
                message_id=id,source='live_message'}
            if self:save(evidence) then self:remember(evidence) end
        end
    end
    function a:forget_channel(channel)
        local prefix=channel..'/'
        for pair in pairs(self.cache) do if pair:sub(1,#prefix)==prefix then self.cache[pair]=nil end end
    end
    return a
end
return M
