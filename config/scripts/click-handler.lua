-- ============================================================================
-- BiliYTPlayer — 鼠标/键盘交互脚本（对齐 B 站网页版操作逻辑）
--   左键单击 = 暂停/播放（300ms 防抖，等双击判定）
--   左键双击 = 全屏切换
-- → 方向键短按 = 快进 +5 秒；长按(>=300ms) = 2.0x 倍速，松开恢复原倍速
-- 由 launch_player 的 --config-dir 自动加载（portable_config/scripts/ 目录）
--
-- 注意：不要在 input.conf 里加任何 RIGHT 绑定，否则会与本脚本双触发。
-- 实测（input=trace）：keydown/keyup 只派发 down/up 给本脚本，不合成 press、
-- 内置默认绑定被脚本 section 压制，up 时 seek 只执行一次。
-- ============================================================================

local CLICK_DELAY = 0.3   -- 单击判定窗口（秒）：大于此间隔的连击视为两次单击
local HOLD_DELAY  = 0.3   -- 右键长按判定阈值（秒）
                          -- 必须 > mpv 键盘自动重复延迟 input-ar-delay（默认 0.2s），
                          -- 保证长按必然先产生 repeat 事件
local DBL_SUPPRESS = 0.15 -- 双击余波压制窗口（秒）：真实双击的事件序列是
                          -- press1 → dbl → press2（SDL 把第二次点击转成 clicks=2
                          -- 的 down/up，mpv 先派发 DBL、后由 up 合成 press）。
                          -- press2 距 dbl 亚毫秒级，150ms 绰绰有余；人类不可能在
                          -- 150ms 内完成「双击后再单击」，不会误伤真实单击。
                          -- 2026-10-27 用户实测 bug：原无此压制，press2 起新定时器，
                          -- 300ms 后误暂停（双击全屏后播放器自己暂停）。

-- ── 左键：单击暂停 / 双击全屏（防抖） ────────────────────────────────────────
local click_timer = nil
local last_dbl_at = -1   -- 最近一次双击的 mpv 时间戳（秒），用于压制双击余波

local function kill_click_timer()
    if click_timer then
        click_timer:kill()
        click_timer = nil
    end
end

mp.add_key_binding("MBTN_LEFT", "click-pause", function()
    -- 双击序列的 press2 是双击余波（距 dbl 亚毫秒），不能起暂停定时器
    if mp.get_time() - last_dbl_at < DBL_SUPPRESS then return end
    kill_click_timer()
    click_timer = mp.add_timeout(CLICK_DELAY, function()
        mp.command("cycle pause")
        click_timer = nil
    end)
end)

mp.add_key_binding("MBTN_LEFT_DBL", "dblclick-fullscreen", function()
    kill_click_timer()  -- 双击：取消本次单击的暂停，直接全屏
    last_dbl_at = mp.get_time()
    mp.command("cycle fullscreen")
end)

-- ── 右方向键：短按快进 / 长按 2 倍速 ─────────────────────────────────────────
local hold_timer = nil
local boosted = false
local prev_speed = 1.0

local function kill_hold_timer()
    if hold_timer then
        hold_timer:kill()
        hold_timer = nil
    end
end

local function on_right_key(event)
    if event == "down" then
        boosted = false
        kill_hold_timer()
        hold_timer = mp.add_timeout(HOLD_DELAY, function()
            prev_speed = mp.get_property_number("speed", 1.0)
            boosted = true
            mp.set_property_number("speed", 2.0)
            mp.osd_message("▶▶ 2.0x", 0.6)
            hold_timer = nil
        end)
    elseif event == "up" then
        kill_hold_timer()
        if boosted then
            mp.set_property_number("speed", prev_speed)  -- 恢复按下前的倍速
            mp.osd_message(("▶ %.1fx"):format(prev_speed), 0.6)
            boosted = false
        else
            mp.command("seek 5")   -- 短按：快进 5 秒
        end
    end
    -- "repeat"（长按自动重复）与 "press"（IPC keypress 产生）均忽略
end

mp.add_key_binding("RIGHT", "right-hold-speed", function(e) on_right_key(e.event) end, {complex = true})
