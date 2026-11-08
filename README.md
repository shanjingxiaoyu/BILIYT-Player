# BiliYTPlayer — 剪贴板直连播放器

> 复制 B 站 / YouTube 链接到剪贴板，自动用内置 mpv 播放。
> 支持 B 站 DASH 直链、杜比视界、HDR 动态映射、YouTube 最高 4K 流。

## 架构

```
bili_yt_player.pyw      GUI 入口（tkinter），剪贴板监听 + 工作线程（生产者-消费者）
bili_clipboard_dolby.py 后端核心：B 站 API 鉴权 / WBI 签名 / DASH 流提取 / mpv 启动参数
bili-ipc.lua            mpv 插件：Ctrl+←/→ 切集按键 → 信号文件 → Python 执行切流
mpv-portable/mpv.exe    播放器（所有解码、渲染、色调映射在此完成）
mpv-portable/yt-dlp.exe YouTube URL 解析（由 mpv 内置 ytdl_hook 调用）
config/mpv.conf         mpv 渲染 / 同步 / 缓冲 / HDR 映射配置（参考副本）
config/input.conf       mpv 快捷键配置（参考副本）
```

播放流程：剪贴板监听线程只做读取 + 正则匹配，检测到新链接入队；工作线程消费队列，调用 B 站 API 或 yt-dlp 解析出真实流地址，启动 mpv 播放。监听线程全程非阻塞。

## 使用

1. 从 **Releases** 下载二进制包（包含 `BiliYTPlayer.exe` + `mpv-portable/`）
2. 解压后双击 `BiliYTPlayer.exe`
3. 复制 B 站 / YouTube 视频链接到剪贴板，自动播放
4. 按 `q` 退出，`f` 全屏，`` ` `` 查看渲染统计（mpv 默认快捷键见 `config/input.conf`）

可选：浏览器安装 `bili_onekey_mpv.user.js` 油猴脚本（Tampermonkey），在 B 站视频页点"⚡ MPV 播放"按钮或按 `Alt+P`，一键复制链接并唤起本地播放。

### 从源码运行

```bash
# 1. 准备 mpv-portable（从 Releases 下载，或自行安装 mpv + yt-dlp）
#    目录结构：mpv-portable/mpv.exe、mpv-portable/yt-dlp.exe、mpv-portable/portable_config/

# 2. 安装依赖
pip install -r requirements.txt

# 3. B 站登录：首次启动会弹出二维码，用 B 站 App 扫码即可
#    凭证自动保存到 %APPDATA%/BiliYTPlayer/.env，无需手工填写

# 4. 运行
pythonw legacy/bili_yt_player.pyw
```

## 打包

```bash
cd legacy
.venv-tk/Scripts/python.exe -m PyInstaller --noconfirm bili_yt_player.spec
# 输出 dist/BiliYTPlayer.exe，与 mpv-portable/ 同级放置
```

> `.venv-tk` 必须基于**系统版 Python** 创建（自带 tkinter）。用托管版 Python
> 建的虚拟环境没有 tkinter，打包不报错但 exe 双击即崩。

## 关键设计

- **B 站直连**：裸 socket HTTPS 直连 B 站 API，避免 `requests` 库在 Windows 下的代理探测延迟
- **YouTube 代理**：mpv 不会自动读取环境变量代理，`launch_player` 会显式传 `--http-proxy`（mpv 拉 CDN 流）和 `--ytdl-raw-options=...,proxy=`（yt-dlp 解析 URL）。代理检测支持环境变量 / Windows 系统代理 / socks5→http 端口探测 / PAC 识别
- **HDR 动态映射**：`mpv.conf` 通过 `profile-cond` 依据视频源色彩空间自动切换 HDR10 / HLG / SDR 映射参数
- **DASH 同步**：`video-sync=audio` 让视频服从音频时钟，避免 B 站 / YouTube 双 CDN 分离流时钟打架导致卡顿

## 维护

非程序员维护指南（存档/验收/发布流程）：见 [`docs/维护手册.md`](docs/维护手册.md)。开发行为准则见 [`.cursorrules`](.cursorrules)。

## 敏感信息声明

仓库不含任何账号凭证。B 站登录态由扫码登录自动保存到用户目录 `%APPDATA%/BiliYTPlayer/.env`，不随仓库分发，请勿分享该文件。播放时凭证通过 HTTP 请求头直接传给 mpv，不写入临时文件。

## 环境要求

- Windows 10/11（x64）
- 运行时：mpv ≥ 0.36（推荐 0.41，需支持 `profile-cond`）、yt-dlp
- 开发：Python 3.10+、PyInstaller
