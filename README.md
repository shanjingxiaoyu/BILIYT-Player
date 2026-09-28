# BiliYTPlayer — 剪贴板直连播放器

> 复制 B 站 / YouTube 链接到剪贴板，自动用内置 mpv 播放。
> 支持 B 站 DASH 直链、杜比视界、HDR 动态映射、YouTube 最高 4K 流、
> **UP 主广告自动跳过**、**B 站弹幕**、**原生字幕（CC / AI）**。

## 架构

```
bili_yt_player.pyw      GUI 入口（tkinter），剪贴板监听 + 工作线程（生产者-消费者）
bili_clipboard_dolby.py 后端核心：B 站 API 鉴权 / WBI 签名 / DASH 流提取 / mpv 启动参数
sponsorblock.py         广告跳过：片段查询 / 过滤 / 载荷构造 / 内嵌 Lua 跳过脚本
danmaku.py              弹幕：下载 / deflate 解压 / XML 解析 / ASS 转换 / 缓存
subtitle.py             原生字幕（CC / AI）：查询 / 下载 / ASS 转换
mpv_osc.py              受管 OSC：给 osc.lua 打补丁（彩色进度条 + 去广告时长）
osc_base.lua            基线 osc.lua（mpv v0.41.0-244-gaf9c81fa1，LGPLv2.1+）
mpv-portable/mpv.exe    播放器（所有解码、渲染、色调映射在此完成）
mpv-portable/yt-dlp.exe YouTube URL 解析（由 mpv 内置 ytdl_hook 调用）
config/mpv.conf         mpv 渲染 / 同步 / 缓冲 / HDR 映射配置（参考副本）
config/input.conf       mpv 快捷键配置（参考副本）
config/sponsorblock.conf.example  广告跳过配置（参考副本）
config/danmaku.conf.example       弹幕配置（参考副本）
config/subtitle.conf.example      字幕配置（参考副本）
```

播放流程：剪贴板监听线程只做读取 + 正则匹配，检测到新链接入队；工作线程消费队列，调用 B 站 API 或 yt-dlp 解析出真实流地址，启动 mpv 播放。监听线程全程非阻塞。

广告跳过流程：工作线程拿到 `cid` 后查询 SponsorBlock 片段 → 过滤分类 → 载荷经环境变量传给 mpv 的 Lua 脚本 → mpv 在播放中比对进度并 seek 跳过。查询失败或超时一律静默降级，不影响播放。

弹幕/字幕流程：工作线程用同一个 `cid` 下载弹幕 XML 并转成 ASS，同时查询原生字幕，
两者作为两条字幕轨交给 mpv（弹幕主轨、字幕次轨）。任何一步失败都降级为「没有弹幕/字幕」，不影响播放。

## 使用

1. 从 **Releases** 下载二进制包（包含 `BiliYTPlayer.exe` + `mpv-portable/`）
2. 解压后双击 `BiliYTPlayer.exe`
3. 复制 B 站 / YouTube 视频链接到剪贴板，自动播放
4. 主窗口有三个勾选框（广告跳过 / 弹幕 / 原生字幕）与三个设置按钮
5. mpv 快捷键：`q` 退出、`f` 全屏、`` ` `` 查看渲染统计、`n` 跳到下一广告片段、`Enter` 撤销跳过

### 主窗口功能

| 控件 | 说明 |
|------|------|
| **自动跳过 UP 主广告** | SponsorBlock 广告跳过总开关 |
| **显示弹幕** | 弹幕总开关 |
| **显示原生字幕** | B 站 CC / AI 字幕开关 |
| **跳过设置…** | 逐分类选择跳过档位、撤销窗口、最短片段长度 |
| **弹幕设置…** | 不透明度、字号、存活时长、显示区域、屏蔽词、清空缓存 |
| **字幕设置…** | 对齐位置（九宫格）、距边距离、字号、颜色、边框样式 |
| **播放历史** | 双击任意条目即可重播该视频 |

所有设置**保存后播放下一个视频即生效**，无需重启。

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

## 弹幕与字幕

### 弹幕

复制 B 站链接播放时，自动下载该视频弹幕并以**外挂 ASS** 显示（不转码、不烧录，直播流同样可用）。

- **无需登录**：弹幕端点是公开接口，游客也可获取
- **不依赖 yt-dlp**：项目本来就走 DASH 直链、手里已有 `cid`，直接用 stdlib 下载并转换
- **保留率高**：轨道分配采用 Danmaku2ASS 式策略（弹幕尾部离开屏幕后轨道即可复用）。
  朴素实现会丢掉约 91% 的弹幕
- **显示区域决定保留率**：可用轨道数 = `画面高度 × display_region ÷ 行高`。
  显示区域调得越小，同时能容纳的弹幕越少。实测（3189 条弹幕的样本）：

  | display_region | 1080p 可用轨道 | 保留率 |
  |---|---|---|
  | 0.30（默认） | 6 | 70.2% |
  | 0.55 | 9 | 81.1% |
  | 0.80 | 13 | 92.9% |

  默认取 0.30 是为了让弹幕集中在画面上部、不干扰下方字幕；
  若你更在意「一条都不漏」，可在「弹幕设置…」里调大该值。
  丢弃过多时日志会给出提示。
- **字号按分辨率自动缩放**：B 站的字号字段是为 1080p 设计的绝对像素，
  直接照搬会让 4K 视频的弹幕只有 1.2% 屏高（小到看不清）。
  现在按 `视频高度 × font_size_ratio` 换算，各分辨率观感一致
- **自上而下紧贴堆叠**：与 B 站官方播放器一致，新弹幕优先占用最上面的空轨道
- **彩色**：保留弹幕原始颜色；滚动/顶部/底部三种模式分别处理
- **避让**：默认占画面上方 30%，下方留给原生字幕与进度条

### 原生字幕（CC / AI 字幕）

部分视频带字幕（实测抽样的视频中约一半有 `ai-zh`）。有则自动加载到**次字幕轨**，
与弹幕同时显示；没有则静默跳过。

**默认底部居中。** 「字幕设置…」按钮可调：

- **对齐位置** — 九宫格（底部/中部/顶部 × 左/中/右），默认**底部 中**
- **距边距离比例** — 视频高度 × 该值，默认 `0.06`（约 6%）
- **字号比例**、**字体**
- **文字颜色 / 底框颜色**
- **不透明度**
- **边框样式** — 不透明底框（推荐，任何画面都清晰）或仅描边
- **恢复默认**

配置写在 `%APPDATA%\BiliYTPlayer\subtitle.conf`（首次运行自动生成，
仓库内 [config/subtitle.conf.example](config/subtitle.conf.example) 为参考副本）。

> **注意**：这里说的「字幕」是 B 站通过 API 提供的字幕。
> UP 主**压制在画面里的硬字幕**是视频像素的一部分，任何播放器都无法调整其位置或样式。

| | 弹幕 | 原生字幕 |
|---|---|---|
| 需要登录 | 否 | 是（复用 SESSDATA） |
| 覆盖率 | 几乎全部视频 | 部分视频 |
| 字幕轨 | 主轨（sid=1） | 次轨（secondary-sid=2） |
| 默认位置 | 画面上方（留白给字幕） | 底部居中 |
| 配置文件 | `danmaku.conf` | `subtitle.conf` |

### 配置与开关

主窗口有三个勾选框：**自动跳过 UP 主广告**、**显示弹幕**、**显示原生字幕**。
**「弹幕设置…」** 可调弹幕的不透明度、字号、滚动/固定存活时长、显示区域、
描边、字体、屏蔽关键词与屏蔽类型，并可一键清空缓存。
**「字幕设置…」** 见上一节。

也可直接编辑对应 conf 文件（首次运行自动生成，
[config/danmaku.conf.example](config/danmaku.conf.example) 与
[config/subtitle.conf.example](config/subtitle.conf.example) 为参考副本）。
**改完播放下一个视频即生效。**

弹幕与字幕的 ASS 缓存在 `%APPDATA%\BiliYTPlayer\cache\`，文件名含分辨率与**样式指纹**
（改了位置/字号会自动生成新文件，不会命中旧缓存），超出上限按最久未用自动清理。

## 关键设计

- **B 站直连**：裸 socket HTTPS 直连 B 站 API，避免 `requests` 库在 Windows 下的代理探测延迟
- **YouTube 代理**：mpv 不会自动读取环境变量代理，`launch_player` 会显式传 `--http-proxy`（mpv 拉 CDN 流）和 `--ytdl-raw-options=...,proxy=`（yt-dlp 解析 URL）。代理检测支持环境变量 / Windows 系统代理 / socks5→http 端口探测 / PAC 识别
- **HDR 动态映射**：`mpv.conf` 通过 `profile-cond` 依据视频源色彩空间自动切换 HDR10 / HLG / SDR 映射参数
- **DASH 同步**：`video-sync=audio` 让视频服从音频时钟，避免 B 站 / YouTube 双 CDN 分离流时钟打架导致卡顿
- **广告跳过分层**：Python 只负责「查什么」（HTTP 查询 + 过滤 + 传参），seek 全部交给 mpv 的 Lua 脚本。mpv 的 Lua 环境没有 HTTP 客户端，因此这个分工是唯一可行解
- **载荷走环境变量**：`--script-opts` 以逗号分隔键值，而 JSON 必然含逗号会被截断，故经 `BSPONSOR_PAYLOAD` 传递
- **受管 OSC 的锚点式补丁**：mpv 内置 OSC 只能在进度条画章节刻度线，无法按任意区间着色，因此以「代码特征锚定」给 osc.lua 打补丁。每个锚点必须在基线中唯一，否则整体放弃并回退内置 OSC —— 这样 mpv 升级不会让播放器变砖
- **弹幕用 stdlib 自实现**：不引入 `yt-dlp-danmaku`/`biliass`。原因：本项目的 `yt-dlp.exe` 是冻结版，内嵌 Python 3.10，而 pip 装的 biliass 是 cp311 编译扩展，ABI 不匹配永远 import 不进去；且 biliass 为 GPLv3
- **弹幕响应要解 deflate**：端点是裸 deflate（无 zlib 头），须 `zlib.decompress(raw, -15)`
- **弹幕 XML 需自行排序**：B 站返回的顺序不是按时间的，直接按输入顺序分配轨道会把保留率压到 27.8%
- **弹幕字号按 1080p 基准缩放**：B 站的 `size` 字段是为 1920×1080 设计的绝对像素，直接当 ASS 字号用会让 4K 弹幕只有 1.2% 屏高
- **次字幕轨要关 ass-override**：mpv 的 `secondary-sub-ass-override` 默认为 `strip`，会把原生字幕的 ASS 样式整段丢弃（字幕变小号白字并跑到顶部）。必须显式设为 `no`
- **字幕位置按比例**：ASS 的 `MarginV` 是 PlayRes 像素，写死会在不同分辨率下观感差异巨大；改为 `height × position_ratio`，跨分辨率一致
- **缓存名带样式指纹**：弹幕与字幕的 ASS 缓存文件名都含样式哈希，改字号/位置后自动重新生成，不会命中旧缓存

## 测试

```bash
python -m unittest discover -s legacy/tests -t .
```

三处**真实 mpv 校验**不可省略（纯文本断言抓不到这些）：

- `TestLuaScriptParses` — 用真实 mpv 加载 SponsorBlock 跳过脚本
- `TestPatchedOscParsesInMpv` / `TestColoredBarsActuallyRender` — 加载补丁版 OSC 并**断言彩色像素真的出现在进度条上**
- `TestDanmakuRendersInMpv` — 渲染弹幕 ASS 并断言弹幕像素出现

这些检查存在的理由都是踩过的坑：`end` 是 Lua 保留字（须写 `s["end"]`）；
`assdraw.new_event()` 产生的 ASS 新事件行**不继承 `\pos`**（色块必须用独立 ass 对象）；
`element.name` 在 mpv OSC 里恒为 nil（选择器须用 `element.type == "slider"`）。
只有让 mpv 真正渲染、并对像素做断言，才会暴露这类静默失败。

## 敏感信息声明

仓库不含任何账号凭证。B 站 SESSDATA 通过本地 `.env` 文件提供（已在 .gitignore 中排除），请勿提交。
`legacy/tests/` 与源码中不包含任何真实 Cookie；弹幕/字幕/SponsorBlock 的测试均使用合成数据或公开接口。

## 第三方组件与许可

| 组件 | 位置 | 许可 |
|------|------|------|
| `osc_base.lua` | `legacy/osc_base.lua` | 取自 mpv 的 `player/lua/osc.lua`（mpv 项目为 LGPLv2.1+ / GPLv2+），commit `af9c81fa1`，仅作运行时打补丁的基线 |
| mpv | `mpv-portable/mpv.exe` | 由 Releases 单独分发，**不在本仓库中** |
| yt-dlp | `mpv-portable/yt-dlp.exe` | 由 Releases 单独分发，**不在本仓库中** |
| BilibiliSponsorBlock 数据 | `bsbsb.top` | 仅调用其公开 API，不包含其代码 |

本仓库**未**引入 `biliass` / `yt-dlp-danmaku`（详见「关键设计」）。
本仓库自身尚未声明 LICENSE。

## 环境要求

- Windows 10/11（x64）
- 运行时：mpv ≥ 0.36（推荐 0.41，需支持 `profile-cond` 与 Lua scripting）、yt-dlp
- 开发：Python 3.10+、PyInstaller
