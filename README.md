# BiliYTPlayer — 剪贴板直连播放器

> 复制 B 站 / YouTube 链接到剪贴板，自动用内置 mpv 播放。
> 支持 B 站 DASH 直链、杜比视界、HDR 动态映射、YouTube 最高 4K 流、**UP 主广告自动跳过**。

## 架构

```
bili_yt_player.pyw      GUI 入口（tkinter），剪贴板监听 + 工作线程（生产者-消费者）
bili_clipboard_dolby.py 后端核心：B 站 API 鉴权 / WBI 签名 / DASH 流提取 / mpv 启动参数
sponsorblock.py         广告跳过：片段查询 / 过滤 / 载荷构造 / 内嵌 Lua 跳过脚本
mpv-portable/mpv.exe    播放器（所有解码、渲染、色调映射在此完成）
mpv-portable/yt-dlp.exe YouTube URL 解析（由 mpv 内置 ytdl_hook 调用）
config/mpv.conf         mpv 渲染 / 同步 / 缓冲 / HDR 映射配置（参考副本）
config/input.conf       mpv 快捷键配置（参考副本）
config/sponsorblock.conf.example  广告跳过配置（参考副本）
```

播放流程：剪贴板监听线程只做读取 + 正则匹配，检测到新链接入队；工作线程消费队列，调用 B 站 API 或 yt-dlp 解析出真实流地址，启动 mpv 播放。监听线程全程非阻塞。

广告跳过流程：工作线程拿到 `cid` 后查询 SponsorBlock 片段 → 过滤分类 → 载荷经环境变量传给 mpv 的 Lua 脚本 → mpv 在播放中比对进度并 seek 跳过。查询失败或超时一律静默降级，不影响播放。

## 使用

1. 从 **Releases** 下载二进制包（包含 `BiliYTPlayer.exe` + `mpv-portable/`）
2. 解压后双击 `BiliYTPlayer.exe`
3. 复制 B 站 / YouTube 视频链接到剪贴板，自动播放
4. 按 `q` 退出，`f` 全屏，`` ` `` 查看渲染统计，`n` 跳到下一广告片段（mpv 默认快捷键见 `config/input.conf`）

### 从源码运行

```bash
# 1. 准备 mpv-portable（从 Releases 下载，或自行安装 mpv + yt-dlp）
#    目录结构：mpv-portable/mpv.exe、mpv-portable/yt-dlp.exe、mpv-portable/portable_config/

# 2. 安装依赖
pip install -r legacy/requirements.txt

# 3. 配置 B 站 SESSDATA（可选，未配置则游客权限）
#    首次运行会弹出浏览器配置页，自动保存到 %APPDATA%\BiliYTPlayer\.env

# 4. 运行
pythonw legacy/bili_yt_player.pyw
```

## 打包

```bash
cd legacy
.venv/Scripts/python.exe -m PyInstaller --noconfirm bili_yt_player.spec
# 输出 dist/BiliYTPlayer.exe，与 mpv-portable/ 同级放置
```

## 广告自动跳过（BilibiliSponsorBlock）

数据来自 [BilibiliSponsorBlock](https://github.com/hanydd/BilibiliSponsorBlock) 的公共服务器
[bsbsb.top](https://www.bsbsb.top)，由网友标注片段。

**交互顺序：先跳过，再提示。** 进入广告片段立即跳过，随后在画面上显示提示与倒计时；
倒计时内按 **Enter** 可撤销这次跳过（回到原位置，且该片段本次不再自动跳过）。

**进度条彩色区间**：跳过的片段会按分类在 mpv 进度条上显示为彩色色块（与浏览器插件一致），
右下角时长显示为「去广告后的时长 (-省下时长)」。

### 分类跳过档位

`sponsor` / `selfpromo` / `exclusive_access` / `interaction` / `poi_highlight` /
`intro` / `outro` / `preview` / `filler` / `music_offtopic` / `padding`

每个分类可设为四档之一（默认值对齐原作者插件）：

| 档位 | 行为 | 默认应用于 |
|------|------|-----------|
| 自动跳过 | 进入片段即跳，可 Enter 撤销 | sponsor、selfpromo、filler、music_offtopic、padding |
| 手动跳过 | 进度条显色，按 `n` 跳 | interaction、poi_highlight、intro、outro |
| 仅在进度条显示 | 只显色，不跳过 | exclusive_access、preview |
| 禁用 | 不查询、不显示 | — |

### 修改配置

GUI 主窗口的 **「跳过设置…」** 按钮可逐类选择档位，并调整提示开关、撤销窗口、
最短片段长度与配色预览。**保存后播放下一个视频即生效**，无需重启。

也可直接编辑 `%APPDATA%\BiliYTPlayer\sponsorblock.conf`（首次运行自动生成，
仓库内 [config/sponsorblock.conf.example](config/sponsorblock.conf.example) 为同内容参考副本）。

### 快捷键

- `n` — 跳到下一个片段（manual 档位下进入片段后按此跳过）
- `Enter` — 撤销刚刚的自动跳过（由脚本强制接管，无需自行绑定）
- 关闭功能：GUI 勾选框取消，或配置里 `enabled = false`（此时连 API 都不请求）

### 降级保证

网络异常、超时、无片段数据时完全不干预播放。若 OSC 补丁因 mpv 版本变化而失效，
会自动回退到 mpv 内置 OSC（彩色进度条消失，但跳过功能与播放均正常）。

## 播放历史

历史列表使用 `ttk.Treeview`，记录时间、来源、标题与画质/音轨，
并额外显示本次跳过的段数与时长的概要。**双击任意条目即可重播该视频**。

## 关键设计

- **B 站直连**：裸 socket HTTPS 直连 B 站 API，避免 `requests` 库在 Windows 下的代理探测延迟
- **YouTube 代理**：mpv 不会自动读取环境变量代理，`launch_player` 会显式传 `--http-proxy`（mpv 拉 CDN 流）和 `--ytdl-raw-options=...,proxy=`（yt-dlp 解析 URL）。代理检测支持环境变量 / Windows 系统代理 / socks5→http 端口探测 / PAC 识别
- **HDR 动态映射**：`mpv.conf` 通过 `profile-cond` 依据视频源色彩空间自动切换 HDR10 / HLG / SDR 映射参数
- **DASH 同步**：`video-sync=audio` 让视频服从音频时钟，避免 B 站 / YouTube 双 CDN 分离流时钟打架导致卡顿
- **广告跳过分层**：Python 只负责「查什么」（HTTP 查询 + 过滤 + 传参），seek 全部交给 mpv 的 Lua 脚本。mpv 的 Lua 环境没有 HTTP 客户端，因此这个分工是唯一可行解
- **载荷走环境变量**：`--script-opts` 以逗号分隔键值，而 JSON 必然含逗号会被截断，故经 `BSPONSOR_PAYLOAD` 传递
- **受管 OSC 的锚点式补丁**：mpv 内置 OSC 只能在进度条画章节刻度线，无法按任意区间着色，因此以「代码特征锚定」给 osc.lua 打补丁。每个锚点必须在基线中唯一，否则整体放弃并回退内置 OSC —— 这样 mpv 升级不会让播放器变砖

## 测试

```bash
python -m unittest discover -s legacy/tests -t .
```

其中两个真实 mpv 校验不可省略：

- `TestLuaScriptParses` — 用真实 mpv 加载跳过脚本
- `TestPatchedOscParsesInMpv` — 用真实 mpv 加载补丁版 OSC

原因：`end` 是 Lua 保留字（须写 `s["end"]`），以及 `assdraw.new_event()` 产生的新 ASS
事件行**不继承 `\pos`**（色块必须用独立 ass 对象）。这两类问题纯文本断言都抓不到，
只有让 mpv 真正加载才会暴露。

## 敏感信息声明

仓库不含任何账号凭证。B 站 SESSDATA 通过本地 `.env` 文件提供（已在 .gitignore 中排除），请勿提交。

## 环境要求

- Windows 10/11（x64）
- 运行时：mpv ≥ 0.36（推荐 0.41，需支持 `profile-cond` 与 Lua scripting）、yt-dlp
- 开发：Python 3.10+、PyInstaller
