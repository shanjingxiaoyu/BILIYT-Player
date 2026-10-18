-- bili-ipc.lua — BiliYTPlayer 切集快捷键转发
-- 组合键切集：Ctrl+← 上一集 / Ctrl+→ 下一集（不占用裸方向键，避免与 seek 冲突）
-- 把意图写入信号文件，由外部 Python 进程轮询读取后执行真正的切流（loadfile）。

local function signal_path()
    local appdata = os.getenv("APPDATA") or "."
    return appdata .. "\\BiliYTPlayer\\bili-nav.signal"
end

local function nav(dir)
    local f = io.open(signal_path(), "w")
    if f then
        f:write(dir)
        f:close()
    end
end

mp.add_key_binding("Ctrl+Left", "bili-prev-episode", function() nav("prev") end)
mp.add_key_binding("Ctrl+Right", "bili-next-episode", function() nav("next") end)

mp.osd_message("BiliYTPlayer: Ctrl+← 上一集 / Ctrl+→ 下一集", 2)
