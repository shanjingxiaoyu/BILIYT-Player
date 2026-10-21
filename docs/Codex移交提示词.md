# Codex 接手 BiliYTPlayer — 移交提示词

> 用法：把下方「---- 提示词开始 ----」到「---- 提示词结束 ----」之间的内容，整段粘贴给 Codex 作为第一条消息。本文件本身也可以直接作为上下文附件交给 Codex。

---

## ---- 提示词开始 ----

你将接手一个名为 **BiliYTPlayer** 的 Windows 桌面项目，请按以下背景和工作纪律执行。所有路径都是绝对路径，直接可用。

### 项目一句话

剪贴板直连播放器：用户复制一条 B 站 / YouTube 链接 → 程序自动解析 → 唤起内置 mpv 播放。技术栈：Python 3.13（tkinter GUI）+ PyInstaller 打包 + mpv 便携版（Windows 命名管道 JSON-RPC IPC）+ yt-dlp。仓库根目录：`C:/Users/Administrator/WorkBuddy/重置版`。

### 第一步：先读文件，再动手

按顺序读：

1. `docs/维护手册.md` —— 必读。含打包命令、13 项发布验收清单、11 条踩坑红线。
2. `git log --oneline`（最近 10 条）—— 这就是本项目的最新修改史，每条提交信息写明了改了什么、为什么。
3. `legacy/bili_yt_player.pyw` —— GUI 入口（剪贴板监听、任务队列、播放调度、扫码登录）。
4. `legacy/bili_clipboard_dolby.py` —— 后端核心（B 站裸 socket API、WBI 签名、DASH/durl 选流、番剧 PGC API、YouTube 限流探测、mpv 启动与 IPC、自动连播、切集）。
5. `legacy/mpv-portable/portable_config/mpv.conf` —— mpv 配置。**改它之前必读红线 #1**。
6. `legacy/bili-ipc.lua` —— mpv 内挂脚本，把 Ctrl+←/→ 快捷键事件转发到命名管道。
7. `.workbuddy/memory/` 下的日志 —— 更细的踩坑记录。

### 工作流纪律（用户会据此验收）

1. **每个需求开工前，先执行 `git add -A && git commit -m "存档：<一句话说明>"`**。用户通常会说「先帮我提交存档」——照做即可。
2. **一次会话只做一个需求**，不要"顺手"改别的。
3. **改完必须真实打包 + 端到端实测**，不接受"理论上应该可行"。未实测的结论要明确标注为推测。
4. Git 提交信息用中文写清改了什么。
5. **架构纪律：装修不推倒**。在能跑通的代码上做精简、合并、修复；禁止推倒重写、禁止引入新框架新依赖除非用户明确要求。
6. 用户偏好：简体中文沟通；交付要结构化（表格 + 摘要）；短问题直接答，技术问题给详细拆解。

### 构建与验证

- **唯一可用构建环境是 `legacy/.venv-tk`**（基于系统版 Python313 创建，自带 tkinter）。WorkBuddy 托管版 Python 没有 tkinter，用它打包 exe 双击即崩（`No module named 'tkinter'`），且 PyInstaller 过程不报错——这是踩过的坑，别重犯。
- 打包：`cd legacy && .venv-tk/Scripts/python.exe -m PyInstaller --noconfirm bili_yt_player.spec`，或双击 `build_exe.bat`。产物 `dist/BiliYTPlayer.exe`（约 14MB），与 `mpv-portable/` 同级放置。
- **打包后必做冒烟**：双击 exe 能起来 + 复制一个 BV 链接能播，才算完成。
- 测试 mpv 相关功能前，先 `taskkill //IM mpv.exe //F` 清掉残留进程，否则 stray mpv 会占用 IPC 命名管道导致属性查询串台、误判 bug。

### 当前状态（截至 2026-10-21）

- git 最新提交：`e358004`（番剧播放走 PGC API + mpv.conf profile 陷阱修复 + 自动连播 E2E 实测通过），此后工作区干净，无未提交改动。
- exe 已重打包，冒烟通过。
- 发布验收 13 项中，用户侧尚未实测的：**#1 BV 播放、#2 多 P、#5 番剧 EP、#6 合集 SS、#8 切集快捷键、#9 自动连播、#11 音轨、#12 历史、#13 退出重开**。其中番剧链路（#5/#6/#8/#9）代码刚修完，是优先复测项。YouTube（#10）已验证可用；限流时自动降级 360p 是服务端行为，属预期，换代理节点可恢复高画质。

### 关键红线（动手前必读，每条都是真金白银的调试时间换来的）

1. **mpv.conf 的 profile 陷阱**：`[xxx]` 段头之后的选项全部归属该 profile，没有"退回全局"的语法。铁律：**全局选项一律放文件上方，条件 profile 一律放最后**。曾因全局选项被困在 `[HLG]` 段内，导致 `keep-open`/`cache`/`hwdec` 只对 HLG 片源生效、连播失效。改完用 IPC 查属性验证：`get_property keep-open` 应为 `true`。
2. **番剧必须走 PGC API**：视频 API（`/x/player/wbi/playurl`）对番剧一律返回 `code=-404`，必须用 `/pgc/player/web/playurl`（`get_playurl_pgc` + `pick_streams_from_result`，已兼容 dash/durl 两种流形态）。免费账号只有渐进式 durl（预期 480p/1080p），大会员才有 DASH——不是 bug。
3. **YouTube 限流探测**：代理出口 IP 被 YouTube 标记时，默认 client 地址被 CDN 锁死（每 URL 只放行前 ~2.5MB，mpv 发 `Range: bytes=0-` 必 403，表现为窗口一闪即逝）。应对是 `launch_player` 播放前的中段 Range 探测，判定限流自动切 `player_client=android`（代价 360p）。`ratebypass` 是服务端签发标记，客户端无法锚定高画质，别在这上面浪费时间。
4. **B 站裸 socket 不要改回 requests**：`_FastBiliSession`/`_fast_bili_api`/`_fast_https_request` 直连是为了绕过 requests/urllib3 的代理探测延迟。timeout 已改为全链路透传（默认 10s）+ 瞬时 stall 重试，别把异常吞掉返回截断数据（曾导致 `[!] timed out`）。
5. **代理端口别写死**：当前系统代理 `127.0.0.1:52459`（曾是 52460，代理工具换过端口）。代码靠 `_resolve_proxy_url` 自动探测，保持这个机制。mpv 不读环境变量，启动参数必须显式传 `--http-proxy` 和 `--ytdl-raw-options proxy=`。
6. **mpv 播放参数只改 mpv.conf**，禁止在 Python 源码里写死渲染/HDR/音频参数（用户明确要求，涉及 OLED 主屏 + SDR 副屏的 HDR tone-mapping 分级，参数已调好）。
7. **自动连播机制**：`keep-open=yes` 会抑制 `end-file`，连播靠 `observe_property('eof-reached')`；`keep-open-pause=no` 是续播前提（EOF 暂停态会被 loadfile 的新文件继承导致卡暂停）；切集用 `loadfile` + `audio-add select`，loadfile 后等 ~1 秒再挂音轨。别在切集代码里补 `set pause no`——会抢用户手动暂停的控制权。
8. **SESSDATA / .env**：登录态无效（SessdataInvalid）才删 .env 跳扫码；网络异常必须保留 .env。GUI 上「重新登录」按钮（原「清空历史」）可在运行中热替换会话。
9. **流优先级**：AV1 (codecid=13) > HEVC (12) > AVC (7)，权重 `{13:3, 12:2, 7:1}`。
10. **测试陷阱**：`--length=N` 会对每个 loadfile 新文件生效导致级联跳集（是测试手法问题不是 bug）；B 站 CDN 直连下载必须带 Referer + cookies 否则 403。

### 常见任务速查

| 想改什么 | 改哪里 |
|---|---|
| mpv 播放/渲染/音频参数 | `legacy/mpv-portable/portable_config/mpv.conf` 全局段（同步参考副本 `config/mpv.conf`） |
| B 站解析逻辑 | `legacy/bili_clipboard_dolby.py`：`_fast_bili_api` / `get_playurl` / `get_playurl_pgc` |
| YouTube 逻辑 | 同上的 `launch_player` YouTube 分支 + `_yt_is_throttled` |
| 快捷键 | `legacy/bili-ipc.lua` + Python 侧信号文件轮询（`_switch_episode`） |
| GUI / 登录 / 监听 | `legacy/bili_yt_player.pyw` |
| 打包 | `legacy/build_exe.bat` 或上面的 PyInstaller 命令（必须 `.venv-tk`） |

### 你的第一个任务

通读以上文件后，用一段话向我复述：项目的播放链路是怎样的、当前哪些功能刚修完待实测、你认为最危险的三个坑是什么。然后再等我的具体需求，不要提前动手改代码。

---- 提示词结束 ----
