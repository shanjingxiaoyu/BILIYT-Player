# 迁移协作契约（Lead 维护，子智能体只读）

> 本文件是并行的**唯一接口约定**。文件归属为硬边界：只改自己名下的文件。

## 文件归属

| 负责人 | 文件 |
|---|---|
| **Lead** | `bili_yt_player_qml.py`、`ui/bridge.py`、`ui/Main.qml`、`ui/theme.qml`、`ui/qmldir`、`build_qml.spec`、`MIGRATION.md`、`README.md` |
| **settings-agent** | `ui/settings/**`（SettingsWindow + 各页签）、`ui/settings_bridge.py`、`app/settings_io.py`(可选) |
| **quality-agent** | `app/quality.py`、`legacy/bili_clipboard_dolby.py`（**仅** `get_playurl` / `pick_dolby_streams` / 新增 PGC 回落）、`_verify_quality.py` |
| **shell-agent** | `app/autostart.py`、`app/tray.py`、`ui/CloseDialog.qml`、`_verify_shell.py` |

`legacy/` 下其余文件（`danmaku.py` / `sponsorblock.py` / `subtitle.py` / `mpv_osc.py` / `osc_base.lua` / `bili_yt_player.pyw`）**谁都不许改**。

## 已完成的地基（可直接用，不要改）

### `app/app_config.py` — 通用设置 `general.conf`

```python
DEFAULT_CONFIG = {
    "close_action": "ask",     # ask | close | tray   → 点 X 之后的行为
    "ask_on_close": True,      # 是否还弹询问框（"以后不再显示"取消勾选后为 False）
    "autostart_mode": "none",  # none | registry        → 开机自启
    "quality": "auto",         # auto | 127 | 126 | 125 | 120 | 116 | 112 | 80 | 64 | 32 | 16
}
load_config(config_dir, log=None) -> dict
save_config(config_dir, cfg) -> bool
```

### `ui/theme.qml` — 设计 token 单例

```qml
import "."            // 或 import ".." （视所在目录）
Theme.dark / card / cardAlt / stroke / text / text2 / text3 / accent /
Theme.hover / sel / windowBg / danger / ok / radius / radiusCard / gap / pageMaxW
```
`Theme.dark` 自动跟随系统；离屏截图脚本用 `__forceScheme="light"/"dark"` 强制。

### 离屏截图验证（**每个界面都必须自证**）

```bash
python _shot.py <qml路径> <输出png> [宽] [高] [light|dark]
```
真实 Fluent 渲染 + 中文字体，无需桌面。注意：QML 里有 `bridge` / `settingsBridge`
等上下文属性时，直接 `_shot.py` 会报未定义 —— 这类页面请写一个**带 mock 的
wrapper qml**（放在自己目录下，如 `ui/settings/_shot_wrapper.qml`）再截。

## 接口约定

### 1. settings-agent 必须导出

`ui/settings_bridge.py`：

```python
class SettingsBridge(QObject):
    configChanged = Signal()      # 任何配置被保存后 emit（Lead 收到后重载 4 份 cfg）
    requestClose  = Signal()      # 设置窗口请求关闭（QML 侧 close() 即可，不需要）
    def __init__(self, config_dir: Path, log=None) -> None
    # 其余成员自定；QML 里通过上下文属性 settingsBridge 访问
```

`ui/settings/SettingsWindow.qml`：根元素必须是 `Window`，且

```qml
Window {
    id: win
    visible: false                 // 默认不显示，由 Bridge 调 open()
    function open() { win.visible = true; win.raise(); }
}
```

Lead 侧加载方式（已定，不要改）：

```python
engine.rootContext().setContextProperty("settingsBridge", settings_bridge)
...
engine.load(QUrl.fromLocalFile(str(ui / "settings" / "SettingsWindow.qml")))
# 之后 window.open()
```

**设置页必须覆盖的落盘目标**（格式/字段名一律不变）：`danmaku.conf`、`subtitle.conf`、
`sponsorblock.conf`、`general.conf`、`.env`(SESSDATA)。范围钳制数字**原样搬**：
见 `legacy/bili_yt_player.pyw` 第 439-478 / 547-570 / 689-712 行。

### 2. quality-agent 必须导出

`app/quality.py`：

```python
QUALITY_OPTIONS = (("auto", "自动（最高可用）"), ("127", "8K"), ("126", "杜比视界"),
                   ("125", "HDR"), ("120", "4K"), ("116", "1080P60"), ("112", "1080P+"),
                   ("80", "1080P"), ("64", "720P"), ("32", "480P"), ("16", "360P"))
def qn_from_pref(pref: str) -> int      # "auto" → 126；其余 → int(pref)
```

`legacy/bili_clipboard_dolby.py` 只允许改成**向后兼容**签名：

```python
def get_playurl(session, bvid, cid, img_key, sub_key, qn: int = 126) -> dict
def pick_dolby_streams(dash: dict, max_qn: int = 0)  # max_qn=0 表示不限制
```

旧调用方（`bili_yt_player.pyw`、`legacy/tests/`）必须继续工作。

### 3. shell-agent 必须导出

```python
# app/autostart.py
def options() -> tuple[tuple[str, str], ...]     # (("none","仅手动启动"), ("registry","开机自启"),)
def is_enabled(mode: str) -> bool
def apply(mode: str, exe_path: str) -> tuple[bool, str]   # (成功, 说明/错误)
def current_exe() -> str                                   # 打包后 sys.executable / 源码运行返回空
```

```python
# app/tray.py
class Tray(QObject):
    def __init__(self, parent=None)                       # 无边框图标可用 QSystemTrayIcon + 生成 QPixmap
    showRequested = Signal()                              # 单击/菜单"显示主窗口"
    quitRequested = Signal()                              # 菜单"退出"
    def show(self); def hide(self); def setTip(self, s: str); def notify(self, title, body)
    @property
    def available(self) -> bool
```

`ui/CloseDialog.qml`：根 `Window`，接口：

```qml
Window {
    property bool dark: false
    property string action: "tray"        // 当前选中：close | tray
    property bool remember: true          // "以后不再显示"（默认勾选）
    signal accepted(string action, bool remember)
    function open()                       // 显示并居中
}
```

## 交付纪律

1. 只改自己名下的文件；改完 `git status --short` 自查，越界立即还原。
2. 每个可运行的逻辑留**一个**自检脚本（`_verify_*.py`），并真实跑过。
3. 界面用 `_shot.py` 截图自证，把截图路径写进汇报。
4. 汇报格式：做了什么 / 自检命令与结果 / 遗留问题 / 涉及文件清单。
