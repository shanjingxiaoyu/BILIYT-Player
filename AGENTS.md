# AGENTS.md — BiliYTPlayer

> 给 AI 协作者的入口。只列硬约束，细节见文末。别把这份扩写成第二本手册。

## 一句话架构

Python（`bili_yt_player.pyw` GUI + 剪贴板监听；`bili_clipboard_dolby.py` 取流）只负责
「拿到 B 站 / YouTube 真实流地址 → 启动 mpv」。解码、渲染、HDR 色调映射、音频输出
**全部在 `mpv-portable/mpv.exe`**。两进程通过 Windows 命名管道
`\\.\pipe\bili-mpv-ipc` 与信号文件 `bili-nav.signal` 通信。

## 硬约束（都是踩过坑才写下的）

1. **画面 / 色彩 / 快捷键 / 音频一律改 mpv 侧**：`mpv.conf`、`input.conf`、Lua。
   禁止在 Python 里造轮子或引入新 UI 库。唯一例外：启动时经 `launch_player` 的
   `cmd` 列表传 CLI 参数（命令行覆盖同名选项）。
2. **`mpv.conf` 全局选项必须写在第一个 `[profile]` 段头之前**。写进段里会静默
   失效——曾导致 `keep-open` / `cache` / `hwdec` 全失效、自动连播不工作。
3. **Lua 的 `string.match` 不支持 `|` 交替**，`"flac|aac"` 永远匹配不到；
   用多个 `string.match(...) ~= nil or ...` 组合。且 `and` 优先级高于 `or`，
   整串 `or` 要加括号。
4. **凭证绝不落盘**：SESSDATA 只走 `--http-header-fields` 直传 mpv。
   写 `%TEMP%` cookie 文件是已修掉的历史债，别再引入。
5. **发布包必须把 `bili-ipc.lua` 放在 exe 同级**：`launch_player` 只查
   `_exe_dir / "bili-ipc.lua"`，找不到就不注入 `--script`，`Ctrl+←/→` 切集
   会静默失效。发布 zip 里也要有这个条目。
6. **配置 / Lua 有多份副本**：`config/`、`legacy/mpv-portable/portable_config/`、
   两个发布目录，改一处要同步其余，zip 需重打包才更新。发布包里不要带
   mpv 的 `cache/` 与 `watch_later/`。
7. **发布版 exe 完全静默**（不写任何日志）；日志仅源码运行时启用。
8. **老标签 / `archive/*` 分支与当前 `main` 无共同祖先**（历史重写过），
   不是"旧版本"，别当垃圾清理。

## 改动纪律

- 改 mpv 相关只动 `mpv.conf`，静默保存；**必须真 mpv 端到端实测**才算完成，
  不接受未验证的理论方案。
- 打包用**系统 Python** 建的 `.venv-tk`；托管版 Python 缺 tkinter，
  打包不报错但 exe 双击即崩。
- 改后端 / 交互后必跑 E2E：`e2e_interaction.py`(13)、`e2e_login.py`(11)、
  `e2e_mpv_lifecycle.py`。生命周期测试会 taskkill 所有 mpv，别在用户看片时跑。
- `git push` 走代理常不通；云端同步用 GitHub Git Data API
  （blob → tree → commit → PATCH ref），并用云端 tree 与本地 HEAD 比对校验。

## 配置以谁为准

`config/mpv.conf` 是**唯一事实来源**，`.cursorrules` 与本文档只是解释层。
2026-11-15 已把 `.cursorrules` 的三处过时描述校正：第 2.5 节（BitPerfect 只认无损）、
第 4 节（`[SDR]` profile 只是通用范式、本项目并未启用）、第 5 节（打包用 `.venv-tk`）。
今后若再出现分歧，以 `mpv.conf` 为准，并顺手把文档改回来——别让 AI 按旧描述改配置。

## 细节去哪看

- `docs/维护手册.md` — 开工 SOP、验收清单、**19 条红线**（每条含根因与修法）
- `.cursorrules` — 完整架构准则、HDR `target-peak` 亮度分档表
- `README.md` — 架构、使用、历史与归档
- `.workbuddy/memory/` — 本项目逐日决策记录
