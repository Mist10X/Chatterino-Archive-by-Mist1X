local core = require('history_core')

local protocol = require('panel_protocol')

local moderation_core = require('moderation_core')

local VERSION = '0.11.0'
local settings = {users={}, channels={}, keep=1000, revision='',auto_trim=false,
    auto_trim_interval=3600,auto_trim_keep=1000,trim_preserved={}}

local bindings, archives, errors = {}, {}, {}

local config_error, trimming, last_request = nil, false, ''

local discovery = 'saved_tabs'
local open_channels = nil
local moderation, moderation_error
local auto_trim_last_ms, auto_trim_elapsed_ms, auto_trim_result = 0, 0, ''

local function now_ms()

    local ok,stamp=pcall(function() return c2.DateTime.current_utc():to_unix_milliseconds() end)

    return ok and stamp or 0

end

local function ensure_moderation()

    if not moderation then moderation=moderation_core.new(io.open,c2.MessageFlag or {},now_ms) end

end



local function read(path, maximum)

    local f, err, code = io.open(path, 'rb')

    if not f then

        if code == 2 then return nil end

        error('Cannot read ' .. path .. ': ' .. tostring(err))

    end

    local text = f:read(maximum or 150001)

    f:close()

    return text or ''

end



local function write(path, text)

    local f, err = io.open(path, 'wb')

    assert(f, err)

    local ok, detail = f:write(text)

    local closed, close_error = f:close()

    assert(ok and closed, detail or close_error)

end



local function notify(channel, text)

    if channel and channel:is_valid() then channel:add_system_message('[History Tools] ' .. text) end

    c2.log(c2.LogLevel.Info, '[History Tools] ' .. text)

end



local function record(message, source, binding_name, previous)

    if trimming then return end

    local user = tostring(core.field(message, 'login_name', '')):lower()

    local channel = tostring(core.field(message, 'channel_name', '')):lower():gsub('^#', '')

    local actual_channel=binding_name or channel

    if settings.channels[actual_channel] and not moderation_error then

        local ok,err=pcall(function()

            ensure_moderation()

            moderation:process(message,actual_channel,source,function()

                local binding=bindings[actual_channel]

                if not binding or not binding.channel:is_valid() then return {} end

                return core.last_messages(binding.channel,binding.channel:count_messages())

            end,previous)

        end)

        if not ok then moderation_error=tostring(err) end

    end

    if not settings.users[user] or not settings.channels[channel] then return end

    local key = user .. '--' .. channel

    if errors[key] then return end

    local ok, err = pcall(function()

        if not archives[key] then archives[key] = core.archive(io.open, {user=user, channel=channel}) end

        local archive = archives[key]

        archive.enabled = true -- The panel configuration is authoritative in version 0.4.

        archive:record(message, source)

    end)

    if not ok then

        errors[key] = tostring(err)

        c2.log(c2.LogLevel.Warning, 'History Tools: ' .. tostring(err))

    end

end



local function scan(binding)

    for _, message in ipairs(core.last_messages(binding.channel, binding.channel:count_messages())) do

        record(message, 'buffer',binding.name)

    end

end



local function configure()

    local text = read('panel-control.txt')

    assert(text, 'Открой панель архива для создания настроек.')

    local new = protocol.parse(text)

    if new.revision ~= settings.revision then

        local previous = settings

        settings = new
        local auto_changed=previous.auto_trim~=new.auto_trim
            or previous.auto_trim_interval~=new.auto_trim_interval
            or previous.auto_trim_keep~=new.auto_trim_keep
        if not auto_changed then
            for channel in pairs(previous.trim_preserved or {}) do if not new.trim_preserved[channel] then auto_changed=true;break end end
            for channel in pairs(new.trim_preserved or {}) do if not (previous.trim_preserved or {})[channel] then auto_changed=true;break end end
        end
        if auto_changed then auto_trim_last_ms=now_ms();auto_trim_elapsed_ms=0;auto_trim_result='' end

        local had_errors = next(errors) ~= nil

        errors = {}

        moderation_error=nil

        -- Rescan only when an enabled user is added/resumed, or when retrying a failed archive.

        local need_scan = had_errors

        for user, enabled in pairs(new.users) do

            if enabled and not previous.users[user] then need_scan = true end

        end

        if need_scan then

            for name, binding in pairs(bindings) do

                if settings.channels[name] and binding.channel:is_valid() then binding.scan = true end

            end

        end

    end

end



local function attach()

    for name, binding in pairs(bindings) do

        if not settings.channels[name] or not binding.channel:is_valid() then

            pcall(function() binding.connection:disconnect() end)

            if binding.replaced then pcall(function() binding.replaced:disconnect() end) end

            if moderation then moderation:forget_channel(name) end

            bindings[name] = nil

        end

    end

    for name in pairs(settings.channels) do

        if not bindings[name] then

            local channel = c2.Channel.by_name(name)

            if channel and channel:is_valid() and channel:get_type() == c2.ChannelType.Twitch then

                local binding = {channel=channel, name=name, scan=true}

                binding.connection = channel:on_message_appended(function(message) record(message, 'live',name) end)

                if type(channel.on_message_replaced)=='function' then

                    binding.replaced=channel:on_message_replaced(function(_,old,message) record(message,'replace',name,old) end)

                end

                bindings[name] = binding

            end

        end

        local binding = bindings[name]

        if binding and binding.scan then binding.scan = false; scan(binding) end

    end

end



local function discover_channels()

    -- Newer forks expose their open windows. Older builds keep using the saved/manual list.

    local found = {}

    local ok = pcall(function()

        assert(c2.windows and type(c2.windows.all) == 'function')

        for _, window in ipairs(c2.windows:all()) do

            local notebook = window.notebook

            if notebook then

                for i = 0, notebook.page_count - 1 do

                    local page = notebook:page_at(i)

                    if page then

                        for _, split in ipairs(page:splits()) do

                            local channel = split.channel

                            if channel and channel:is_valid() and channel:get_type() == c2.ChannelType.Twitch then

                                found[core.name(channel:get_name())] = true

                            end

                        end

                    end

                end

            end

        end

    end)

    discovery = ok and 'open_tabs' or 'saved_tabs'
    open_channels = ok and found or nil
    if ok then for channel in pairs(found) do if not (settings.excluded or {})[channel] then settings.channels[channel] = true end end end

end



local function trim_channel(channel, keep)

    assert(channel and channel:is_valid() and channel:get_type() == c2.ChannelType.Twitch,

           'Канал не открыт в Chatterino.')

    trimming = true

    local ok, retained, removed = pcall(core.trim, channel, keep, c2.MessageContext and c2.MessageContext.Repost)

    trimming = false

    if not ok then error(retained) end

    return retained, removed

end



local function ack(request, state, detail)

    write('panel-ack.json', '{"id":' .. core.quote(request.id) .. ',"status":' .. core.quote(state)

        .. ',"detail":' .. core.quote(detail) .. '}\n')

end



local function process_request()

    local request = protocol.request(read('panel-request.txt'))

    if not request or request.id == last_request then return end

    -- Persist acceptance before clearing anything: a restart must never replay a stale clear.

    ack(request, 'processing', 'Запрос принят. Если приложение прервалось, проверь чат перед повторением.')

    last_request = request.id

    local ok, detail = pcall(function()
        if request.targets then
            discover_channels()
            assert(open_channels, 'Не удалось проверить открытые вкладки. Очистка не выполнена.')
            for name in pairs(open_channels) do
                assert(request.channels[name], 'Список открытых каналов изменился. Открой окно очистки заново.')
            end
            for name in pairs(request.channels) do
                assert(open_channels[name], 'Список открытых каналов изменился. Открой окно очистки заново.')
            end
            local channels = {}
            for _, name in ipairs(request.targets) do
                local channel = c2.Channel.by_name(name)
                assert(channel and channel:is_valid() and channel:get_type() == c2.ChannelType.Twitch,
                    'Канал недоступен: ' .. name .. '. Очистка не выполнена.')
                channels[name] = channel
            end
            local lines, failures, removed_total = {}, 0, 0
            for _, name in ipairs(request.targets) do
                local success, retained, removed = pcall(trim_channel, channels[name], request.keep)
                if success then
                    removed_total = removed_total + removed
                    lines[#lines+1] = '#' .. name .. ': оставлено ' .. retained .. ', убрано ' .. removed
                else
                    failures = failures + 1
                    lines[#lines+1] = '#' .. name .. ': ошибка — ' .. tostring(retained)
                end
            end
            local result = 'Каналов обработано: ' .. (#request.targets-failures) .. '/' .. #request.targets
                .. '. Убрано сообщений: ' .. removed_total .. '. Исключения и архив на диске сохранены.\n' .. table.concat(lines, '\n')
            if failures > 0 then error(result) end
            return result
        end
        local retained, removed = trim_channel(c2.Channel.by_name(request.channel), request.keep)
        return 'Оставлено ' .. retained .. ', убрано ' .. removed .. '. Архив на диске сохранён.'

    end)

    ack(request, ok and 'ok' or 'error', tostring(detail))

end

local function process_auto_trim()
    auto_trim_elapsed_ms=auto_trim_elapsed_ms+2000
    if not settings.auto_trim then return end
    local now=now_ms()
    local due=(now>0 and auto_trim_last_ms>0 and now-auto_trim_last_ms>=settings.auto_trim_interval*1000)
        or ((now<=0 or auto_trim_last_ms<=0) and auto_trim_elapsed_ms>=settings.auto_trim_interval*1000)
    if not due then return end
    auto_trim_last_ms=now;auto_trim_elapsed_ms=0
    if not open_channels then auto_trim_result='Не удалось получить открытые каналы';return end
    local targets={}
    for channel in pairs(open_channels) do
        if not (settings.trim_preserved or {})[channel] then targets[#targets+1]=channel end
    end
    table.sort(targets)
    local removed_total,failures=0,0
    for _,name in ipairs(targets) do
        local channel=c2.Channel.by_name(name)
        if channel and channel:is_valid() and channel:get_type()==c2.ChannelType.Twitch
                and channel:count_messages()>settings.auto_trim_keep then
            local ok,_,removed=pcall(trim_channel,channel,settings.auto_trim_keep)
            if ok then removed_total=removed_total+removed else failures=failures+1 end
        end
    end
    auto_trim_result='Обработано каналов: '..#targets..'; убрано сообщений: '..removed_total
    if failures>0 then auto_trim_result=auto_trim_result..'; ошибок: '..failures end
end



local function publish()

    local channels, problem = {}, config_error or ''

    local published = {}
    for name in pairs(settings.channels) do published[name] = true end
    for name in pairs(open_channels or {}) do published[name] = true end
    for name in pairs(published) do

        local twitch_id=''

        pcall(function()

            local channel=bindings[name] and bindings[name].channel

            if channel and channel:is_valid() then

                local value=tostring(channel:get_twitch_id())

                if value:match('^%d+$') and #value<=25 then twitch_id=value end

            end

        end)

        channels[#channels+1] = '{"name":' .. core.quote(name) .. ',"attached":'

            .. (bindings[name] and bindings[name].channel:is_valid() and 'true' or 'false')
            .. ',"open":' .. (open_channels and open_channels[name] and 'true' or 'false')
            .. ',"twitch_id":'..core.quote(twitch_id)..'}'

    end

    for key, detail in pairs(errors) do problem = problem .. '\n' .. key .. ': ' .. detail end

    if moderation_error then problem=problem..'\nМодерация: '..moderation_error end

    write('panel-status.json', '{"version":' .. core.quote(VERSION) .. ',"revision":'

        .. core.quote(settings.revision) .. ',"channels":[' .. table.concat(channels, ',')

        .. '],"trim_except":true,"auto_trim":'..(settings.auto_trim and 'true' or 'false')
        .. ',"auto_trim_interval":'..tostring(settings.auto_trim_interval)..',"auto_trim_keep":'..tostring(settings.auto_trim_keep)
        .. ',"auto_trim_result":'..core.quote(auto_trim_result)..',"discovery":' .. core.quote(discovery) .. ',"moderation_unparsed":'..tostring(moderation and moderation.unparsed or 0)
        .. ',"error":' .. core.quote(problem) .. '}\n')

end



local function tick()

    local ok, err = pcall(configure)

    config_error = not ok and tostring(err) or nil

    discover_channels()

    local attached, attach_error = pcall(attach)

    if not attached then config_error = tostring(attach_error) end

    local requested, request_error = pcall(process_request)

    if not requested then config_error = tostring(request_error) end

    local auto_trimmed, auto_trim_error = pcall(process_auto_trim)

    if not auto_trimmed then auto_trim_result='Ошибка: '..tostring(auto_trim_error) end

    local published, publish_error = pcall(publish)

    if not published then c2.log(c2.LogLevel.Warning, 'History Tools status: ' .. tostring(publish_error)) end

    c2.later(tick, 2000)

end



local function command(fn)

    return function(ctx)

        local ok, err = pcall(fn, ctx)

        if not ok then notify(ctx.channel, tostring(err)) end

        return ''

    end

end

assert(c2.register_command('/htrim', command(function(ctx)

    local keep = tonumber(ctx.words[2]) or settings.keep

    local retained, removed = trim_channel(ctx.channel, keep)

    notify(ctx.channel, 'Оставлено ' .. retained .. ', убрано ' .. removed .. '. Архив сохранён.')

end)))

assert(c2.register_command('/hstatus', command(function(ctx)

    local users, connected = 0, 0

    for _, enabled in pairs(settings.users) do if enabled then users = users + 1 end end

    for _ in pairs(bindings) do connected = connected + 1 end

    notify(ctx.channel, 'Версия ' .. VERSION .. '. Записываемых пользователей: ' .. users

        .. '; подключённых каналов: ' .. connected .. '. ' .. (config_error or 'Управление — в панели архива.'))

end)))

assert(c2.register_command('/hhelp', command(function(ctx)

    notify(ctx.channel, 'Управление записью и просмотр сообщений — в отдельной панели «Архив Chatterino by Mist1X».'

        .. ' /hstatus — состояние; /htrim [число] — очистка буфера. Архивы сохраняются на диске.')

end)))

assert(c2.register_command('/hlog', command(function(ctx)

    notify(ctx.channel, 'Запись теперь включается и останавливается кнопками в панели «Архив Chatterino by Mist1X».')

end)))

assert(c2.register_command('/hcheck', command(function(ctx)

    write('selfcheck.txt', 'History Tools by Mist1X 0.11.0 read/write check\n')

    assert(read('selfcheck.txt') == 'History Tools by Mist1X 0.11.0 read/write check\n', 'Read/write failed')

    notify(ctx.channel, 'Чтение и запись работают. Сообщений в Twitch не отправлено.')

end)))

local ok, previous = pcall(read, 'panel-ack.json')

if ok and previous then last_request = previous:match('"id":"([a-zA-Z0-9_-]+)"') or '' end

-- Cancel an unaccepted request left over from a previous process. A clear must be intentional now.

local request_ok, pending = pcall(function() return protocol.request(read('panel-request.txt')) end)

if request_ok and pending and (pending.id ~= last_request or (previous and previous:find('"status":"processing"',1,true))) then
    last_request = pending.id

    pcall(ack, pending, 'error', 'Старый запрос очистки отменён после перезапуска. При необходимости нажми кнопку ещё раз.')

end

tick()

c2.log(c2.LogLevel.Info, 'History Tools ' .. VERSION .. ' initialized')

