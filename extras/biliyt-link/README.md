# BiliYTPlayer 一键唤起（配套组件）

网页点一下悬浮按钮 -> `biliyt://` 协议 -> 本地转发器把链接写进剪贴板 ->
运行中的 BiliYTPlayer 自动捡起并播放。**播放器本体零改动**。

> 这是主项目的配套组件，不是独立产品。脚本沿用旧版名称
> 「⚡ BiliYTPlayer 一键 MPV 播放」和相同 `@namespace`：装过旧版的用户
> 直接覆盖安装就是升级，不会同时存在两个悬浮按钮。旧版纯复制脚本已移除。

## 文件

| 文件 | 作用 |
|---|---|
| `biliyt_launcher.user.js` | 油猴脚本（浏览器端），带 `@updateURL` 自动更新 |
| `biliyt_forwarder.py` | 转发器源码 |
| `register_biliyt_protocol.py` / `.reg` | 注册 / 注销 `biliyt://` 协议 |
| `安装.bat` / `卸载.bat` | 一键安装 / 卸载 |
| `forwarder_config.example.json` | 配置模板；安装时生成实际的 `forwarder_config.json` |

**`biliyt-forwarder.exe` 不入库**（约 7.4MB）。获取方式：
- 已装过配套组件：直接把本机那份 `biliyt-forwarder.exe` 复制到本目录
- 自行打包：`pyinstaller --onefile biliyt_forwarder.py`
- 或等本仓库发布 `link-*` Release 资产后下载

## 安装（三步）

1. 把 `biliyt-forwarder.exe` 放到本目录，双击 `安装.bat`
   （没自动找到播放器时，把 `BiliYTPlayer.exe` 拖到 bat 上再运行一次）
2. Tampermonkey 安装 `biliyt_launcher.user.js`（拖进浏览器或粘贴保存）
3. 打开 B 站 / YouTube 视频页，点右下角 ▶ 按钮

脚本带 `@updateURL`（jsDelivr CDN），**网络可达时** Tampermonkey 会自动更新；
若该 CDN 在你所在网络不可达，升级时把本文件重新拖进 Tampermonkey 即可，
不影响一键唤起功能。备用方案：把 `.user.js` 作为 Release 资产发布后，
把 `@updateURL` 改成该资产地址（github.com 通道）。

若你当前装的是 v1.1.0（无 `@updateURL`），**需要手动重装一次**本文件。
