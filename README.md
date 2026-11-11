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

可选（一键唤起）：安装 [`extras/biliyt-link/`](extras/biliyt-link/) 配套组件——网页端装油猴脚本、本机装 `biliyt://` 转发器，在 B 站 / YouTube 视频页点一下 ▶ 按钮就直接唤起播放，不用手动复制网址。三步安装见该目录 README。脚本沿用旧名称「⚡ BiliYTPlayer 一键 MPV 播放」，装过旧版的直接覆盖安装即可升级。

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

## 历史与归档

本节说明仓库里那些看起来像「另一个项目」的东西，避免误认为仍在维护或误删。

**现行项目**：`main` 分支。项目曾用名 `CB-to-MPV` → `BiliYTPlayer`，仓库现名 `BILIYT-Player`。旧的仓库名 `Bili--smplayer-` 由 GitHub 自动重定向到本仓库，**不是另一个仓库**。可下载版本见 Releases 页。

**为什么要专门说明**：2026-10 前后仓库历史做过一次重置。因此下面的标签和分支与当前 `main` **没有共同祖先**，是重命名前项目的独立代码线，不是现版本的「旧版本」。删掉它们会永久失去那条线上的独有文件。

### 归档分支（只读保留，不再维护）

| 分支 | 时期 | 内容 |
|---|---|---|
| `archive/cb-player-pre-reset-2026-10` | CB-to-MPV 时期 | 自动打包 CI 配置、Cookie 泄漏 / IPC 配对 / 单实例三个测试、早期实现 |
| `archive/worktree-exe-pre-reset-2026-07` | 2026-07 | PyInstaller 打包修复实验，旧目录布局 |

### 历史标签（无 Release 资产，仅用于回溯）

`v1.2.0`、`v2.0`、`v2.1.0`、`v2.2.0`、`v2.3.0`，均为 2026-07 的早期版本里程碑。其中 `v1.2.0` 与 `v2.0` 指向同一提交（项目重命名）。**保留用于历史回溯，不代表可用版本**，请勿据此下载使用。

### 其它仓库

`archive-AutoEq-fork-2023` 是 2023 年 fork 的耳机均衡项目，与本播放器无关，已标注为遗留归档。

## 敏感信息声明

仓库不含任何账号凭证。B 站登录态由扫码登录自动保存到用户目录 `%APPDATA%/BiliYTPlayer/.env`，不随仓库分发，请勿分享该文件。播放时凭证通过 HTTP 请求头直接传给 mpv，不写入临时文件。

## 环境要求

- Windows 10/11（x64）
- 运行时：mpv ≥ 0.36（推荐 0.41，需支持 `profile-cond`）、yt-dlp
- 开发：Python 3.10+、PyInstaller
