local HttpService = game:GetService("HttpService")
local Players = game:GetService("Players")
local player = Players.LocalPlayer

local DEFAULT_SERVER_URL = "http://127.0.0.1:5000/update"
local hb_count = 0
local loaded_sent = false

-- Móc hàm request của Executor
local httprequest = (syn and syn.request) or (http and http.request) or http_request or (fluxus and fluxus.request) or request

local cached_server_url = nil

local function getServerUrl()
    if cached_server_url and cached_server_url ~= "" then
        return cached_server_url
    end
    if getgenv().Url_server and getgenv().Url_server ~= "" then
        cached_server_url = getgenv().Url_server
        pcall(function()
            print("⚡ [NONAME Check online] Lấy URL từ getgenv(): " .. cached_server_url)
        end)
        return cached_server_url
    end
    
    local json_files = {"NONAME/check_online.json", "check_online.json", "noname_server_url.json", "server_url.json"}
    
    for _, fname in ipairs(json_files) do
        pcall(function()
            local content = nil
            if isfile and isfile(fname) then
                content = readfile(fname)
            elseif readfile then
                content = readfile(fname)
            end
            
            if content and content ~= "" then
                local data = HttpService:JSONDecode(content)
                if type(data) == "table" then
                    local url = data.Url_server or data.url or data.Url
                    if url and type(url) == "string" and string.sub(url, 1, 4) == "http" then
                        cached_server_url = url
                    end
                end
            end
        end)
        if cached_server_url then
            return cached_server_url
        end
    end
    
    return DEFAULT_SERVER_URL
end

-- Khởi tạo giải quyết URL ngay lập tức khi load script
task.spawn(function()
    cached_server_url = getServerUrl()
end)

-- Ghi HWID thực tế từ Executor vào file NONAME/data.json trong workspace
task.spawn(function()
    pcall(function()
        local hwid_fn = gethwid or get_hwid or (Delta and Delta.GetHWID)
        if hwid_fn then
            local real_hwid = tostring(hwid_fn())
            if real_hwid and #real_hwid >= 16 then
                local hwid_payload = HttpService:JSONEncode({
                    hwid = real_hwid
                })
                pcall(function()
                    if makefolder and isfolder and not isfolder("NONAME") then
                        makefolder("NONAME")
                    end
                end)
                if writefile then
                    pcall(function() writefile("NONAME/data.json", hwid_payload) end)
                    pcall(function() writefile("data.json", hwid_payload) end)
                end
                print("NONAME_SIGNAL|HWID|" .. real_hwid)
            end
        end
    end)
end)

local function sendPing()
    pcall(function()
        if not player then player = Players.LocalPlayer end
        
        local userid_str = "Unknown"
        local username_str = "Loading..."
        
        if player then
            if player.UserId and player.UserId > 0 then
                userid_str = tostring(player.UserId)
            end
            if player.Name and player.Name ~= "" and player.Name ~= "Loading..." then
                username_str = player.Name
            end
        end

        -- Gửi tín hiệu LOADED duy nhất 1 lần khi player đã load xong ID
        if not loaded_sent and userid_str ~= "Unknown" then
            loaded_sent = true
            task.spawn(function()
                local s_url = getServerUrl()
                pcall(function()
                    print("NONAME_SIGNAL|" .. userid_str .. "|LOADED|" .. s_url)
                end)
                pcall(function()
                    game:GetService("StarterGui"):SetCore("SendNotification", {
                        Title = "⚡ NONAME Check online",
                        Duration = 6
                    })
                end)
            end)
        end

        hb_count = (hb_count % 180) + 1
        local current_time = tostring(os.time())
        
        -- 1. Ghi Logcat (Chạy đồng bộ, không chờ mạng)
        pcall(function()
            print("NONAME_SIGNAL|" .. userid_str .. "|HEARTBEAT|" .. current_time .. "|" .. tostring(hb_count))
        end)
        
        -- 2. Ghi file đĩa cứng backup (Chạy đồng bộ, không chờ mạng)
        if writefile and userid_str ~= "Unknown" then
            pcall(function()
                writefile(userid_str .. ".heartbeat", current_time .. "|" .. tostring(hb_count))
            end)
        end
        
        -- 3. Gửi HTTP bất đồng bộ hoàn toàn (Chạy trong task.spawn, game lag/mạng đơ cũng không ảnh hưởng)
        if userid_str ~= "Unknown" and username_str ~= "Loading..." and username_str ~= "" then
            task.spawn(function()
                local server_url = getServerUrl()
                local data = {
                    ["username"] = username_str,
                    ["userid"]   = userid_str,
                    ["jobid"]    = tostring(game.JobId),
                    ["hb"]       = hb_count
                }
                
                local jsonData = HttpService:JSONEncode(data)
                local sent_ok = false
                
                -- Cách 1: Gửi POST qua httprequest (Không block thread)
                if httprequest then
                    pcall(function()
                        httprequest({
                            Url = server_url,
                            Method = "POST",
                            Headers = { ["Content-Type"] = "application/json" },
                            Body = jsonData,
                            Timeout = 4
                        })
                        sent_ok = true
                    end)
                end
                
                -- Cách 2: Fallback POST qua PostAsync
                if not sent_ok then
                    pcall(function()
                        HttpService:PostAsync(server_url, jsonData, Enum.HttpContentType.ApplicationJson)
                        sent_ok = true
                    end)
                end
                
                -- Cách 3: Fallback qua HttpGet GET
                if not sent_ok then
                    pcall(function()
                        local get_ping_url = server_url .. "?uid=" .. userid_str .. "&username=" .. HttpService:UrlEncode(username_str) .. "&status=online&hb=" .. tostring(hb_count)
                        game:HttpGet(get_ping_url, true)
                    end)
                end
            end)
        end
    end)
end

-- =========================================================
-- [LUA RADAR V3]: BỘ QUÉT LỖI SIÊU TỐC QUA GUISERVICE & COREGUI
-- =========================================================
local Gui = game:GetService("GuiService")
local CoreGui = game:GetService("CoreGui")
local VirtualInputManager = game:GetService("VirtualInputManager")

local function AutoDismissErrorWindow()
    pcall(function()
        Gui:ClearError()
        local promptOverlay = CoreGui:FindFirstChild("RobloxPromptGui") and CoreGui.RobloxPromptGui:FindFirstChild("promptOverlay")
        if promptOverlay then
            local errorPrompt = promptOverlay:FindFirstChild("ErrorPrompt")
            if errorPrompt and errorPrompt.Visible then
                for _, btn in pairs(errorPrompt:GetDescendants()) do
                    if btn:IsA("TextButton") and (btn.Text == "Ok" or btn.Text == "OK") and btn.Visible then
                        if firesignal then pcall(function() firesignal(btn.MouseButton1Click) end) end
                        local absPos = btn.AbsolutePosition
                        local absSize = btn.AbsoluteSize
                        local guiInset = Gui:GetGuiInset()
                        local centerX = absPos.X + (absSize.X / 2)
                        local centerY = absPos.Y + (absSize.Y / 2) + guiInset.Y
                        pcall(function()
                            VirtualInputManager:SendMouseButtonEvent(centerX, centerY, 0, true, game, 0)
                            task.wait(0.05)
                            VirtualInputManager:SendMouseButtonEvent(centerX, centerY, 0, false, game, 0)
                        end)
                    end
                end
            end
        end
    end)
end

task.spawn(function()
    while task.wait(1) do
        pcall(function()
            local m = Gui:GetErrorMessage()
            local promptOverlay = CoreGui:FindFirstChild("RobloxPromptGui") and CoreGui.RobloxPromptGui:FindFirstChild("promptOverlay")
            local promptText = ""

            if promptOverlay then
                local errorPrompt = promptOverlay:FindFirstChild("ErrorPrompt")
                if errorPrompt and errorPrompt.Visible then
                    for _, desc in pairs(errorPrompt:GetDescendants()) do
                        if desc:IsA("TextLabel") and desc.Text ~= "" then
                            promptText = promptText .. " " .. desc.Text
                        end
                    end
                end
            end

            local combinedMsg = string.lower((m or "") .. " " .. promptText)

            if combinedMsg ~= "" and combinedMsg ~= " " then
                -- Nếu là lỗi Hop Server Full / Dịch Chuyển Thất Bại (Mã 772, Máy chủ đã đầy, Teleport failed)
                if string.find(combinedMsg, "772") 
                   or string.find(combinedMsg, "máy chủ đã đầy") 
                   or string.find(combinedMsg, "server is full") 
                   or string.find(combinedMsg, "dịch chuyển") 
                   or string.find(combinedMsg, "teleport failed") then

                    print("⚠️ [Full Server / Teleport Failed] - Không báo kick, tự động đóng cửa sổ lỗi!")
                    AutoDismissErrorWindow()
                    task.wait(2)
                else
                    -- Các lỗi Kick / Disconnect thực sự khác mới báo kick
                    if not player then player = Players.LocalPlayer end
                    local current_file_id = "Unknown"
                    if player and player.UserId and player.UserId > 0 then
                        current_file_id = tostring(player.UserId)
                    end

                    local function notifyLocalKick(reason, raw_msg)
                        task.spawn(function()
                            local server_url = getServerUrl()
                            local data = {
                                ["username"]  = player and player.Name or "Unknown",
                                ["userid"]    = current_file_id,
                                ["jobid"]     = tostring(game.JobId),
                                ["is_kicked"] = true,
                                ["reason"]    = reason,
                                ["err_msg"]   = raw_msg and raw_msg:sub(1, 100) or ""
                            }
                            local success_encode, jsonData = pcall(function()
                                return HttpService:JSONDecode(data)
                            end)
                            if success_encode and jsonData then
                                if httprequest then
                                    pcall(function()
                                        httprequest({
                                            Url = server_url,
                                            Method = "POST",
                                            Headers = { ["Content-Type"] = "application/json" },
                                            Body = jsonData,
                                            Timeout = 5
                                        })
                                    end)
                                else
                                    pcall(function()
                                        HttpService:PostAsync(server_url, jsonData, Enum.HttpContentType.ApplicationJson)
                                    end)
                                end
                            end
                        end)
                    end

                    local reason = "KICK_UI"
                    if string.find(combinedMsg, "kick") then 
                        reason = "KICKED"
                    elseif string.find(combinedMsg, "disconnect") or string.find(combinedMsg, "lost") or string.find(combinedMsg, "279") then 
                        reason = "DISCONNECT"
                    end

                    notifyLocalKick(reason, m ~= "" and m or promptText)
                    if writefile and current_file_id ~= "Unknown" then
                        pcall(function()
                            writefile(current_file_id .. ".disconnect", reason .. "|" .. combinedMsg:sub(1,64) .. "|" .. tostring(os.time()))
                        end)
                    end
                    task.wait(5)
                end
            end
        end)
    end
end)

-- Vòng lặp gửi tim định kỳ 8 giây (Tối ưu hóa I/O và nhảy nhịp tim cực nhanh ngay khi vừa load vào game)
task.spawn(function()
    local wait_count = 0
    while (not player or not player.UserId or player.UserId <= 0) and wait_count < 20 do
        task.wait(0.5)
        player = Players.LocalPlayer
        wait_count = wait_count + 1
    end
    sendPing()
    while task.wait(8) do
        sendPing()
    end
end)
