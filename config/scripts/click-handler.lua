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
-- ── 双击余波压制（2026-11-09 改为事件驱动，不再赌时间） ────────────────────
-- 真实双击序列：press1 → dbl → press2。SDL 派发 DBL 之后，还会由第二次点击
-- 的 mouseup 合成一个 MBTN_LEFT press；**它到达的时间不固定**（点击节奏、
-- 系统负载、事件队列积压都会拉长间隔）。
-- 10-27 用固定 150ms 窗口压制，遇到慢双击 / 高负载时 press2 晚于窗口到达 →
-- 又起 300ms 暂停定时器 →「放大了，过一会儿也暂停了」（11-09 用户复报，偶发）。
-- IPC 实测复现：dbl→press2 间隔 20ms 正常，250ms 即误暂停。
-- 改法：双击后吞掉**接下来的第一个 press**，与它晚到多久无关；另设 0.6s
-- guard（覆盖 Windows 默认双击间隔 500ms）兜底，防止 press2 因平台差异没来时
-- 开关永远挂着、后续单击全被吞。
local DBL_GUARD = 0.6

-- ── 左键：单击暂停 / 双击全屏（防抖） ────────────────────────────────────────
local click_timer = nil
local suppress_next_press = false
local dbl_guard_timer = nil

local function kill_click_timer()
    if click_timer then
        click_timer:kill()
        click_timer = nil
    end
end

local function clear_dbl_guard()
    if dbl_guard_timer then
        dbl_guard_timer:kill()
        dbl_guard_timer = nil
    end
    suppress_next_press = false
end

mp.add_key_binding("MBTN_LEFT", "click-pause", function()
    -- 双击余波 press2：一律吞掉，不看时间（修偶发误暂停的关键）
    if suppress_next_press then
        clear_dbl_guard()
        return
    end
    kill_click_timer()
    click_timer = mp.add_timeout(CLICK_DELAY, function()
        mp.command("cycle pause")
        click_timer = nil
    end)
end)

mp.add_key_binding("MBTN_LEFT_DBL", "dblclick-fullscreen", function()
    kill_click_timer()  -- 双击：取消本次单击的暂停，直接全屏
    suppress_next_press = true
    if dbl_guard_timer then dbl_guard_timer:kill() end
    dbl_guard_timer = mp.add_timeout(DBL_GUARD, function()
        suppress_next_press = false
        dbl_guard_timer = nil
    end)
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
