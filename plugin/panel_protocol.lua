local M = {}
local function valid_name(s) return s and #s <= 64 and s:match('^[a-z0-9_]+$') end
function M.parse(text)
    assert(type(text) == 'string' and #text <= 150000, 'Configuration too large')
    text = text:gsub('\r\n', '\n')
    assert(text:sub(1, 4) == 'HT2\n' and text:sub(-5) == '\nEND\n', 'Incomplete configuration')
    local c = {users={}, channels={}, excluded={}, keep=1000}
    local n = 0
    for line in text:sub(5, -6):gmatch('[^\n]+') do
        local rev = line:match('^revision\t([a-zA-Z0-9_-]+)$')
        local keep = line:match('^keep\t(%d+)$')
        local user, enabled = line:match('^user\t([a-z0-9_]+)\t(on)$')
        if not user then user, enabled = line:match('^user\t([a-z0-9_]+)\t(off)$') end
        local channel = line:match('^channel\t([a-z0-9_]+)$')
        local excluded = line:match('^exclude\t([a-z0-9_]+)$')
        if rev and #rev <= 80 then c.revision = rev
        elseif keep and tonumber(keep) >= 1 and tonumber(keep) <= 1000000 then c.keep = tonumber(keep)
        elseif valid_name(user) then c.users[user] = enabled == 'on'; n = n + 1
        elseif valid_name(excluded) then c.excluded[excluded] = true
        elseif valid_name(channel) then c.channels[channel] = true
        else error('Invalid configuration line') end
    end
    assert(c.revision and n <= 500, 'Invalid configuration revision or too many users')
    for channel in pairs(c.excluded) do c.channels[channel] = nil end
    return c
end
function M.request(text)
    if type(text) ~= 'string' or #text > 150000 then return nil end
    text = text:gsub('\r\n', '\n')
    if text:match('^HT2\ntrim_except\t') then
        local id, keep, body = text:match('^HT2\ntrim_except\t([a-zA-Z0-9_-]+)\t(%d+)\n(.*)END\n$')
        if not id or #id > 80 or tonumber(keep) ~= 1000 then return nil end
        local channels, excluded, count = {}, {}, 0
        for line in body:gmatch('([^\n]*)\n') do
            local kind, value = line:match('^(%a+)\t([a-z0-9_]+)$')
            if not valid_name(value) or (kind ~= 'channel' and kind ~= 'exclude') then return nil end
            local target = kind == 'channel' and channels or excluded
            if target[value] then return nil end
            target[value] = true; count = count + 1
            if count > 2000 then return nil end
        end
        if body:sub(-1) ~= '\n' or not next(channels) then return nil end
        local targets = {}
        for channel in pairs(excluded) do if not channels[channel] then return nil end end
        for channel in pairs(channels) do if not excluded[channel] then targets[#targets+1] = channel end end
        if #targets == 0 then return nil end
        table.sort(targets)
        return {id=id, keep=1000, channels=channels, excluded=excluded, targets=targets}
    end
    local id, channel, keep = text:match('^HT2\ntrim\t([a-zA-Z0-9_-]+)\t([a-z0-9_]+)\t(%d+)\nEND\n$')
    if not id or #id > 80 or not valid_name(channel) then return nil end
    keep = tonumber(keep)
    if keep < 1 or keep > 1000000 then return nil end
    return {id=id, channel=channel, keep=keep}
end
return M
