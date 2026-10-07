# CB-to-MPV — B 站 / YouTube 剪贴板直连播放器

> 复制链接即播。绕过网页播放器，由 mpv + yt-dlp 直取流媒体，画质不降级，零遥测。

常驻后台 → 复制视频链接 → 约 1 秒内 mpv 弹出播放。程序自带一个状态窗口，实时显示探测结果和播放历史。**内置便携版 mpv 与 yt-dlp，开箱即用。**

主程序 `cb_to_mpv.pyw` 只用 Python 标准库，**零第三方依赖**；所有站点解析（签名、DASH 分轨、画质选择）都交给 mpv 的 `ytdl_hook` + yt-dlp，因此站点改算法时通常只需更新 `yt-dlp.exe`，不用改本项目代码。

---

## 支持的链接格式

| 类型 | 格式 | 示例 |
|------|------|------|
| B 站视频 | `bilibili.com/video/BV…`（含 `m.` 移动端域名、无协议头） | `https://www.bilibili.com/video/BV1GJ411x7h7` |
| B 站短链 | `b23.tv/…`（手机 App 分享默认格式） | `https://b23.tv/Ab1Cd2E` |
| 裸 BV 号 | 文本中任意位置的 `BV` + 10 位字符 | `【视频标题】 BV1GJ411x7h7` |
| 番剧 / 电影 | `bangumi/play/ep…`、`bangumi/play/ss…`、`bangumi/media/md…` | `bilibili.com/bangumi/play/ep780621` |
| 课程 | `cheese/play/ep…`、`cheese/play/ss…` | — |
| 直播 | `live.bilibili.com/{房间号}` | `https://live.bilibili.com/196` |
| 音频 | `bilibili.com/audio/au…` | — |
| 收藏夹 | `space.bilibili.com/{uid}/favlist?fid=…`、`bilibili.com/medialist/detail/ml…` | — |
| 合集 / 系列 | `space.bilibili.com/{uid}/lists/{sid}` | — |
| 播放列表 | `bilibili.com/list/…`、`bilibili.com/medialist/play/ml…` | — |
| 稍后再看 | `bilibili.com/watchlater` | 需登录 |
| YouTube | `watch?v=`、`youtu.be/`、`shorts/`、`live/`、`embed/`（含 `m.` / `music.` 子域） | `https://youtu.be/dQw4w9WgXcQ` |

多 P 视频的 `?p=N`、收藏夹的 `?fid=`、合集的 `?sid=` 会保留；其余跟踪参数（`spm_id_from` 等）会被剥除。

短链会由程序自行跟随一次 302 重定向解析成真实地址（yt-dlp 本身不认 `b23.tv`）。

**刻意不支持**：UP 主主页（`space.bilibili.com/{uid}/video`）和动态（`t.bilibili.com/…`、`bilibili.com/opus/…`）。复制这两类链接的意图通常是分享一个人或一条帖子，不是"立刻开始播放"；动态还经常是纯图文，没有可播的视频。

### 播放列表与直播

收藏夹、合集、播放列表、稍后再看会让 mpv 载入**多集播放列表**并从第一集开始。切集用 mpv 默认键位：`<` 上一个、`>` 下一个（`input.conf` 未覆盖，仍然有效）。

直播没有时长和进度条，不能 seek，`save-position-on-quit` 对其无意义。

---

## 安装

### 开箱即用（推荐）

下载 [Release](https://github.com/shanjingxiaoyu/BILIYT-Player/releases) 中的 `CB-to-MPV.zip`，解压后双击 `CB-to-MPV.exe`。不需要安装 Python，也不需要安装 mpv。

解压后的目录结构：

```
CB-to-MPV/
├── CB-to-MPV.exe          主程序（打开一个状态窗口）
├── config.ini             配置文件
├── cookies.txt            Cookie（默认为空模板，即游客模式）
└── mpv-portable/
    ├── mpv.exe            便携版播放器（v0.41）
    ├── yt-dlp.exe         解析引擎
    └── portable_config/   mpv.conf / input.conf
```

### 从源码运行

```bash
git clone https://github.com/shanjingxiaoyu/BILIYT-Player.git
cd BILIYT-Player
python cb_to_mpv.pyw        # 无需 venv，无需 pip install
```

源码运行同样会自动探测同级 `mpv-portable/mpv.exe`。若没有便携版 mpv，程序会回退到系统 `PATH` 中的 `mpv`。

界面用的是标准库 `tkinter`，官方 Python 安装包自带。但精简版解释器（embedded 包、部分第三方发行版）会剥掉 tcl/tk，此时启动或打包会报 `ModuleNotFoundError: No module named 'tkinter'`——换官方完整安装包即可，不需要 `pip install` 任何东西。打包用的解释器同样要带 tcl/tk，否则 PyInstaller 收不进去。

### 主窗口

| 区域 / 按钮 | 作用 |
|------|------|
| 状态 | 与 `cb_to_mpv.log` 同源的内容：探测结果、`>> 链接`、错误 |
| 播放历史 | 每次触发播放记一行（时间 + 站点 + 视频标识）。标题和编码格式在 mpv 侧解析、不回传，所以这里不显示 |
| 暂停监听 / 继续监听 | 暂停期间不再拉起 mpv；期间复制的内容只更新基线，恢复时不会补播 |
| 扫码登录 B 站 | 弹出二维码窗口，用手机 B 站 App 确认；成功后自动写入 `cookies.txt` |
| 清空历史 | 只清播放历史区，不动日志文件 |
| 退出 | 等价于点窗口右上角的 × |

### 退出

关闭主窗口即退出程序；正在播放的 mpv 不受影响，会继续播完。

（重复启动是安全的：命名互斥锁保证只有一个实例在监听，多开的那个会弹一个提示框然后退出。）

---

## 配置

编辑 `config.ini`，全部为可选项，改完重启程序生效。发布包里已带这个文件；源码运行时仓库里没有它（本机配置不入库），需要自定义就自己复制一份：`copy config.template.ini config.ini`——没有 `config.ini` 也能正常跑，程序走内置默认值。

```ini
[paths]
; 一般无需填写，程序会自动探测 mpv-portable/ 下的文件
; mpv_path = C:\path\to\mpv.exe
; ytdlp_path = C:\path\to\yt-dlp.exe
; cookies_file = C:\path\to\cookies.txt

[network]
; YouTube 需要代理时填写
; proxy = http://127.0.0.1:7890

[cookies]
; 推荐方式：直接借用已登录浏览器的 Cookie，无需导出文件
; 可填 chrome / edge / firefox / brave / opera
browser_cookie =

[app]
; true 则在「启动」文件夹创建 CB-to-MPV.lnk
; 仅对 CB-to-MPV.exe 生效；源码运行时会指向当前解释器 + 脚本
autostart = false
```

### 画质与登录

游客模式下 B 站实测能拿到 **1080P / 30 帧**（AV1、HEVC、AVC 三种编码都有），YouTube 为普通画质。再往上的 **4K、1080P 高码率、杜比视界**需要登录，而且**账号本身要有相应权限**（大会员 / 已购买）——登录只是必要条件，不是充分条件。

| 内容 | 游客 | 登录后 |
|------|------|--------|
| 普通视频、公开收藏夹、合集、播放列表、直播、音频 | ✅ 封顶 1080P | ✅ 按账号权限开放 4K / 高码率 |
| 稍后再看 | ✗ | ✅ |
| 私有收藏夹 / 私有播放列表 | ✗ | ✅ 需本人账号 |
| 大会员专属番剧、电影 | ✗ | ✅ 需大会员账号 |
| 付费课程 | ✗ | ✅ 需已购买该课程的账号 |

三种登录方式，优先级 **文件 > 浏览器 > 游客**：

**方式一（推荐）：主窗口点「扫码登录 B 站」**

用手机 B 站 App 扫一扫、在手机上确认即可。拿到的 Cookie 会被写进 `cookies.txt`，**下一个复制的链接就按登录态解析，不用重启**。

- 走的是 B 站网页版同一套官方登录接口（申请二维码 → 手机确认 → 轮询取回服务端新签发的会话 Cookie），你的密码和手机端的登录态都不经过本程序。
- 登录态每 30 分钟复核一次（调 B 站 `nav` 接口）。判定失效会**自动弹出扫码窗口**；从未登录过的游客模式不会被打扰。
- 写文件是**合并**而不是覆盖：`cookies.txt` 会被 yt-dlp 回写它自己攒下的 B 站反爬 Cookie（`buvid3` / `b_nut`），整份重写等于把这些指纹丢掉。
- Cookie 到期后不会被"续期"，服务端不认就是失效，重新扫一次即可。

**方式二**：`config.ini` 里设 `browser_cookie = edge`，由 yt-dlp 直接从浏览器取 Cookie。新版 Chrome / Edge 因 App-Bound Encryption 常常失败，日志会写明具体原因。

**方式三**：装浏览器插件 *Get cookies.txt LOCALLY*，在 B 站页面导出，内容粘贴进 `cookies.txt`。

> `cookies.txt` 含登录凭证（等同于你的登录态），已在 `.gitignore` 中，不要提交或分享。日志里只会出现 Cookie 的字段名，不会出现值。

未配置 Cookie 时复制"稍后再看"链接，日志里会直接写明原因。

---

## 运行日志

程序在自身目录写 `cb_to_mpv.log`，记录本次运行的探测结果、识别到的链接和错误。**每次启动覆盖**，不累积。

排查"复制了没反应"时看这个文件（或窗口里的「状态」区，两者内容一致）。

---

## 播放行为

命令行传入 mpv 的参数：

- `--force-window=yes` 避免 DASH 分轨加载时黑屏
- `--hwdec=auto-safe` 优先 D3D11VA 硬解，失败回退软解
- `--ontop` 窗口置顶，不被浏览器遮挡
- `--ytdl-format=bestvideo[height<=2160]+bestaudio/bestvideo+bestaudio/best`

其余画质、HDR 色调映射（`bt.2446a` + `target-colorspace-hint`）、去带、音频输出（WASAPI）、缓冲策略见 `mpv-portable/portable_config/mpv.conf`，快捷键见同目录 `input.conf`。

注意：命令行参数优先级高于 `mpv.conf`，因此 `mpv.conf` 里的 `hwdec` 与 `ytdl-format` 实际被命令行覆盖。

---

## 自行打包

```bash
pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm cb_to_mpv.spec
# 产物：dist\CB-to-MPV.exe（约 11.5MB，UPX 压缩）
```

体积几乎全是 tkinter 运行库（`tcl86t` + `tk86t` + `tcl/` 脚本，未压缩约 11.4MB）；唯一的第三方依赖 `qrcode` 只增加约 60KB。构建用的 Python **必须自带 tkinter**——精简版解释器（无 `tcl/` 目录）打包会直接失败。

推 `v*` 格式的 tag 会触发 GitHub Actions：自动打包 exe、下载 mpv 与 yt-dlp、把仓库内的 `config.template.ini` 与 `cookies.template.txt` 改名成 `config.ini` / `cookies.txt` 放进发布包、校验完整性后发布 `CB-to-MPV.zip`。见 `.github/workflows/ci.yml`。

---

## 文件说明

| 文件 | 说明 |
|------|------|
| `cb_to_mpv.pyw` | 主程序：剪贴板监听 / 链接识别 / 短链解析 / 扫码登录 / 拉起 mpv |
| `test_cookie_canary.py` | 硬约束守卫：Cookie 的值不许进 `cb_to_mpv.log`。本地 `python test_cookie_canary.py`，CI 的 lint job 已接入；不联网、不碰真的 `cookies.txt` |
| `cb_to_mpv.spec` | PyInstaller 打包配置 |
| `requirements.txt` | 唯一的第三方依赖（`qrcode`，扫码登录画二维码用） |
| `config.template.ini` | 配置模板（全注释默认值），发布时改名为 `config.ini`；本机 `config.ini` 是运行时配置，不入库（已 gitignore） |
| `cookies.template.txt` | Cookie 空模板，发布时改名为 `cookies.txt` |
| `mpv-portable/portable_config/` | mpv 配置（仓库内唯一保留的便携版内容） |
| `dist_portable/` | 成品目录：exe + `mpv-portable/` + `config.ini` + `README.txt`，整目录拷走即用 |

---

## FAQ

**Q：复制链接没反应？**
按顺序查 `cb_to_mpv.log`：
1. 有没有 `>> https://...` 一行？没有说明链接格式没被识别（见上方支持列表）。
2. 有 `>>` 但 mpv 没弹出，看是否有 `找不到 mpv` 或 `拉起 mpv 失败`。
3. `b23.tv` 短链解析需要能访问 `b23.tv`，失败会在日志里写 `短链解析失败`。

**Q：提示找不到 mpv？**
确认 `mpv-portable\mpv.exe` 与 exe 同目录，或在 `config.ini` 的 `[paths]` 里手动指定 `mpv_path`。

**Q：YouTube 打不开？**
在 `config.ini` 的 `[network]` 里填 `proxy`。YouTube 解析依赖代理可达。

**Q：画质只有 1080P？**
游客模式上限如此，配置 Cookie 后重启。

**Q：同一个链接复制第二次不播？**
程序会去重，避免重复弹窗。想重播请先复制别的链接，或直接在 mpv 里重开。

**Q：开机自启后怎么关？**
`config.ini` 里设 `autostart = false` 并重启一次程序（会自动删除启动文件夹里的快捷方式），或手动删除 `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\CB-to-MPV.lnk`。

---

## License

MIT
