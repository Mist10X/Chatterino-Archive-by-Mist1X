-- Local storage and history operations; no network or process execution.
local M = {}

function M.name(value)
    local s = tostring(value or ""):lower():gsub("^[@#]", "")
    assert(s:match("^[a-z0-9_]+$") and #s <= 64, "Invalid channel/user name")
    return s
end

function M.quote(value)
    local escapes = {['"'] = '\\"', ['\\'] = '\\\\', ['\n'] = '\\n',
                     ['\r'] = '\\r', ['\t'] = '\\t'}
    return '"' .. tostring(value or ""):gsub('[%z\1-\31\\"]', function(c)
        return escapes[c] or string.format("\\u%04x", c:byte())
    end) .. '"'
end

-- Gregorian calendar conversion. Does not need Lua's unavailable os library.
function M.iso_utc(ms)
    if type(ms) ~= "number" or ms ~= ms or ms <= 0 or ms > 253402300799999 then
        return "unknown"
    end
    local seconds = math.floor(ms / 1000)
    local days = math.floor(seconds / 86400)
    local z = days + 719468
    local era = math.floor(z / 146097)
    local doe = z - era * 146097
    local yoe = math.floor((doe - math.floor(doe / 1460) + math.floor(doe / 36524)
                          - math.floor(doe / 146096)) / 365)
    local y = yoe + era * 400
    local doy = doe - (365 * yoe + math.floor(yoe / 4) - math.floor(yoe / 100))
    local mp = math.floor((5 * doy + 2) / 153)
    local day = doy - math.floor((153 * mp + 2) / 5) + 1
    local month = mp + (mp < 10 and 3 or -9)
    if month <= 2 then y = y + 1 end
    local daytime = seconds - days * 86400
    return string.format("%04d-%02d-%02dT%02d:%02d:%02d.%03dZ", y, month, day,
        math.floor(daytime / 3600), math.floor(daytime / 60) % 60, daytime % 60,
        math.floor(ms) % 1000)
end

local function field(msg, key, fallback)
    local ok, value = pcall(function() return msg[key] end)
    if not ok or value == nil then return fallback end
    return value
end
M.field = field

function M.reply(msg,channel)
    local cached=field(msg,'archive_reply',nil)
    if cached then return cached end
    local flags=c2 and c2.MessageFlag or {}
    local present=(tonumber(field(msg,'flags',0)) & (tonumber(flags.ReplyMessage) or 0))~=0
    local reply={state='unavailable',channel=channel,user='',display_name='',text='',thread_id='',source='chatterino_reply'}
    local ok=pcall(function()
        if type(msg.elements)~='function' then return end
        local marker=c2 and c2.MessageElementFlag and tonumber(c2.MessageElementFlag.RepliedMessage) or 0
        if marker==0 then return end
        for _,el in ipairs(msg:elements()) do
            if (tonumber(field(el,'flags',0)) & marker)~=0 then
                present=true
                local words=field(el,'words',nil)
                local pieces={}
                if words then for _,word in ipairs(words) do pieces[#pieces+1]=tostring(word) end end
                local text=table.concat(pieces,' ')
                if field(el,'type','')=='single-line-text' then
                    reply.text=text;reply.state='available'
                    local link=field(el,'link',{})
                    reply.thread_id=tostring(field(link,'value',''))
                elseif field(el,'type','')=='text' and text:sub(1,1)=='@' then
                    reply.display_name=text:gsub('^@',''):gsub(':$','')
                    local link=field(el,'link',{})
                    local user=tostring(field(link,'value','')):lower()
                    if user:match('^[a-z0-9_]+$') and #user<=64 then reply.user=user end
                end
            end
        end
    end)
    if not present then return nil end
    if not ok then reply.state='unavailable' end
    -- A ViewThread link identifies the root, not necessarily the direct parent.
    -- Preserve its text/author without inventing a direct-parent ID or timestamp.
    return reply
end

function M.encode_reply(reply)
    if not reply then return 'null' end
    return '{"state":'..M.quote(reply.state)..',"channel":'..M.quote(reply.channel)
        ..',"user":'..M.quote(reply.user)..',"display_name":'..M.quote(reply.display_name)
        ..',"text":'..M.quote(reply.text)..',"thread_id":'..M.quote(reply.thread_id)
        ..',"source":'..M.quote(reply.source)..'}'
end

function M.encode_message(msg, channel, source, reply)
    local stamp = tonumber(field(msg, "server_received_time", 0)) or 0
    if stamp <= 0 then stamp = tonumber(field(msg, "parse_time", 0)) or 0 end
    return '{"id":' .. M.quote(field(msg, "id", ""))
        .. ',"time_utc":' .. M.quote(M.iso_utc(stamp))
        .. ',"channel":' .. M.quote(channel)
        .. ',"user":' .. M.quote(field(msg, "login_name", ""))
        .. ',"display_name":' .. M.quote(field(msg, "display_name", ""))
        .. ',"user_id":' .. M.quote(field(msg, "user_id", ""))
        .. ',"source":' .. M.quote(source)
        .. ',"text":' .. M.quote(field(msg, "message_text", ""))
        .. ',"reply":' .. M.encode_reply(reply or M.reply(msg,channel)) .. '}'
end

local function append(open, path, text)
    local f, err = open(path, "ab")
    assert(f, "Cannot open " .. path .. ": " .. tostring(err))
    local ok, result, write_error = pcall(function() return f:write(text) end)
    local closed, close_result, close_error = pcall(function() return f:close() end)
    assert(ok and result and closed and close_result,
        "Cannot save " .. path .. ": " .. tostring(write_error or close_error or result))
end
M.append = append

function M.archive(open, config)
    local user, channel = M.name(config.user), M.name(config.channel)
    local base = user .. "--" .. channel
    local a = {path = base .. ".jsonl", state_path = base .. ".state",
        user = user, channel = channel, enabled = false, seen = {}, reply_saved={}, count = 0, open = open}

    -- A state journal prevents an interrupted rewrite from forgetting the setting.
    local state, err, code = open(a.state_path, "rb")
    if not state and code ~= 2 then error("Cannot read recording state: " .. tostring(err)) end
    if state then
        for line in state:lines() do
            if line == "on" then a.enabled = true end
            if line == "off" then a.enabled = false end
        end
        state:close()
    end
    local saved, read_error, read_code = open(a.path, "rb")
    if not saved and read_code ~= 2 then error("Cannot read archive: " .. tostring(read_error)) end
    if saved then
        for line in saved:lines() do
            -- IDs emitted by this plugin are restricted to safe UUID-like strings.
            -- Ignore an unfinished final record left by a power failure.
            if line:sub(-1) == "}" then
                local id = line:match('^%{"id":"([a-zA-Z0-9_:%-]+)"')
                if id and not a.seen[id] then
                    a.seen[id] = true
                    a.count = a.count + 1
                end
                if id then
                    local state=line:match(',"reply":%{"state":"([a-z_]+)"')
                    if state then a.reply_saved[id]=math.max(a.reply_saved[id] or 0,state=='available' and 2 or 1) end
                end
            end
        end
        saved:close()
    end
    function a:set_enabled(enabled)
        -- Verify the archive is writable immediately, even before the first message.
        if enabled then append(self.open, self.path, "") end
        append(self.open, self.state_path, "\n" .. (enabled and "on" or "off") .. "\n")
        self.enabled = enabled
    end
    function a:record(msg, source)
        if not self.enabled then return false end
        local login = tostring(field(msg, "login_name", "")):lower()
        local msg_channel = tostring(field(msg, "channel_name", "")):lower():gsub("^#", "")
        if login ~= self.user or msg_channel ~= self.channel then return false end
        local id = tostring(field(msg, "id", ""))
        -- Synthetic/service messages have no stable Twitch ID and aren't archived.
        if not id:match("^[a-zA-Z0-9_:%-]+$") or #id > 200 then return false end
        local reply=M.reply(msg,self.channel)
        local quality=reply and (reply.state=='available' and 2 or 1) or 0
        if self.seen[id] and quality<=(self.reply_saved[id] or 0) then return false end
        append(self.open, self.path, "\n" .. M.encode_message(msg, self.channel, source,reply) .. "\n")
        if not self.seen[id] then self.count = self.count + 1 end
        self.seen[id] = true
        self.reply_saved[id]=quality
        return true
    end
    return a
end

local function last_messages(channel, count)
    local snapshot = channel:message_snapshot(count)
    local list = {}
    for i = 1, #snapshot do list[i] = snapshot[i] end
    local last = channel:last_message()
    if #list > 1 and last then
        local last_id = field(last, "id", "")
        local function same(a, b)
            if a == b then return true end
            return last_id ~= "" and field(a, "id", "") == field(b, "id", "")
        end
        if not same(list[#list], last) then
            assert(same(list[1], last), "Unknown snapshot order; history unchanged")
            local reverse = {}
            for i = #list, 1, -1 do reverse[#reverse + 1] = list[i] end
            list = reverse
        end
    end
    return list
end
M.last_messages = last_messages

function M.trim(channel, keep, repost)
    assert(type(keep) == "number" and keep >= 1 and keep % 1 == 0 and keep <= 1000000,
        "Keep must be a whole number from 1 to 1000000")
    assert(repost ~= nil, "MessageContext.Repost unavailable; history unchanged")
    assert(type(channel.clear_messages) == "function" and type(channel.add_message) == "function",
        "History API unavailable; history unchanged")
    local before = channel:count_messages()
    if before <= keep then return before, 0 end
    -- Keep strong references to all original messages until restoration succeeds.
    local original = last_messages(channel, before)
    assert(#original == before, "Incomplete snapshot; history unchanged")
    local retained = {}
    for i = before - keep + 1, before do retained[#retained + 1] = original[i] end
    local ok, err = pcall(function()
        channel:clear_messages()
        for _, msg in ipairs(retained) do channel:add_message(msg, repost) end
        assert(channel:count_messages() == keep, "Unexpected retained message count")
    end)
    if not ok then
        local restored, restore_error = pcall(function()
            channel:clear_messages()
            for _, msg in ipairs(original) do channel:add_message(msg, repost) end
            assert(channel:count_messages() == before, "Incomplete rollback")
        end)
        if not restored then
            error("Restore failed: " .. tostring(restore_error) .. "; initial error: " .. tostring(err))
        end
        error("Original history restored after error: " .. tostring(err))
    end
    return keep, before - keep
end

return M
