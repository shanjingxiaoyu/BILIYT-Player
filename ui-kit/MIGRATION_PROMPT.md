# 任务：把 BILIYTPlayer 的 UI 从 tkinter 迁移到 PySide6 + QML

## 你在改哪个项目

```
E:\work\pythondata\newprogram\BILIYT-Player-main
```

一个 B站/YouTube 剪贴板嗅探播放器。**先读完 `.cursorrules`（131 行）再动手**，里面写死了架构边界和一堆实测踩坑记录，那些约束优先级高于本提示词。

现行架构（`.cursorrules:5-18`）：

```
bili_yt_player.pyw     1240 行   GUI 入口（tkinter），剪贴板监听 + 工作线程
bili_clipboard_dolby.py  46 KB    后端：B站 API / WBI 签名 / DASH 流提取 / mpv 参数
sponsorblock.py          33 KB    广告跳过：片段查询 / 分类过滤 / 载荷构造
danmaku.py               25 KB    弹幕：下载 / 解压 / XML → ASS
subtitle.py              15 KB    原生字幕 → ASS
mpv_osc.py               13 KB    给 mpv 的 osc.lua 打补丁
mpv-portable/mpv.exe    120 MB    播放器本体（画面/音频/色彩映射全在这）
```

**播放画面、进度条、弹幕渲染全部在 mpv 里，QML 完全碰不到。** 你只负责重写那个 1240 行的 GUI 入口。

---

## 第一原则：后端一行都不要动

以下文件**零改动**：

```
bili_clipboard_dolby.py
sponsorblock.py
danmaku.py
subtitle.py
mpv_osc.py
osc_base.lua
legacy/tests/ 下的 3 个测试
```

`.cursorrules:21` 明确写着「禁止在 Python 中造轮子」——画面显示、色彩空间、HDR 映射、播放快捷键、音量调整，这些全部归 mpv，你连碰都不要碰。

**你要改的只有一个文件 `bili_yt_player.pyw`，以及新增 QML 文件。** 如果发现某个功能必须改后端才能工作，先停下来问，不要自作主张。

---

## 目标：换成什么

| | 现在 | 目标 |
|---|---|---|
| UI 框架 | tkinter / ttk | **PySide6 6.9.2 + QML（Qt Quick）** |
| 样式 | ttk 默认 | **`QQuickStyle.setStyle("FluentWinUI3")`** |
| 动效 | 无 | **`ui-kit/euikit/` 那 5 个组件** |

`ui-kit/` 目录已经准备好，里面有：
- `README.md` — 组件包完整文档，**先读这个**
- `euikit/` — 5 个 QML 组件 + qmldir

**不要引入任何第三方 UI 库**（不要 EvolveUI 全套、不要 PyQt-Fluent-Widgets、不要 QtMaterial）。外观用 PySide6 自带的 FluentWinUI3 样式，动效用手写的 5 个 spring 组件。

---

## 现有代码的精确结构（我已经量过了）

### 主窗口 `_build_ui` — 第 258-336 行，79 行

```
main_frame          ttk.Frame(padding=15)      → 整个窗口
  ├ Label           "剪贴板直连播放器 — B站 / YouTube"  font=("Microsoft YaHei",12,"bold")
  ├ log_frame       ttk.LabelFrame("状态")
  │   └ status_text tk.Text(height=10, width=56, state=disabled, font=("Consolas",9))
  ├ hist_frame      ttk.LabelFrame("播放历史（双击条目重播）")
  │   └ hist_tree   ttk.Treeview(columns=(time,platform,title,quality), show=headings)
  │       + 双击绑定 <Double-1> → _on_history_double_click
  ├ btn_frame       ttk.Frame
  │   └ 6 个 ttk.Button: 暂停监听 / 清空历史 / 跳过设置… / 弹幕设置… / 字幕设置… / 退出
  └ sw              ttk.Frame
      └ 3 个 ttk.Checkbutton:
           自动跳过 UP 主广告 (SponsorBlock)  → self.skip_var
           显示弹幕                          → self.dm_var
           显示原生字幕                      → self.cc_var
```

### 4 个设置对话框（合计 414 行，是 UI 代码的大头）

| 方法 | 行范围 | 行数 | 内容 |
|---|---|---|---|
| `_open_subtitle_settings` | 362-478 | 117 | 字幕字号/位置/颜色/描边/字体 |
| `_open_danmaku_settings` | 479-599 | 121 | 8 个数值输入 + 3 个屏蔽类型开关 |
| `_open_settings` | 600-726 | 127 | SponsorBlock 分类档位（2 个 Combobox + 多个 Checkbutton） |
| `_show_sessdata_page` | 845-893 | 49 | 首次配置：输入 SESSDATA |

`_open_danmaku_settings` 的 `row()` 辅助函数（第 502-508 行）是个模式，8 个数值项都走它：

```python
def row(r, label, var, width=8):
    ttk.Label(grid, text=label, width=18, anchor="w").grid(row=r, column=0, sticky="w", pady=3)
    e = ttk.Entry(grid, textvariable=var, width=width)
    e.grid(row=r, column=1, sticky="w")
    return e

# 8 个配置项
row(0, "不透明度 (0-1)",     opacity_var,   cfg.get("opacity", 0.7))
row(1, "字号比例",           fontsize_var,  cfg.get("font_size_ratio", 0.04))
row(2, "滚动存活(秒)",       marquee_var,   cfg.get("duration_marquee", 12.0))
row(3, "固定存活(秒)",       still_var,     cfg.get("duration_still", 5.0))
row(4, "显示区域 (0-1)",     region_var,    cfg.get("display_region", 0.30))
row(5, "描边宽度",           outline_var,   cfg.get("outline", 2.0))
row(6, "字体",               face_var,      width=22)
row(7, "屏蔽关键词",         kw_var,        width=22)
```

`do_save()` 里有一套**范围钳制逻辑**（第 556-575 行），**必须原样保留**，每一条都是用户实测反馈的 bug 修复：

```python
new["opacity"]        = max(0.05, min(1.0,  _num(opacity_var, 0.7)))
new["font_size_ratio"]= max(0.01, min(0.20, _num(fontsize_var, 0.04)))
new["duration_marquee"]=max(2.0,  min(30.0, _num(marquee_var, 12.0)))
new["duration_still"] = max(1.0,  min(20.0, _num(still_var, 5.0)))
new["display_region"] = max(0.3,  min(1.0,  _num(region_var, 0.30)))   # 低于 0.3 轨道太少
new["outline"]        = max(0.0,  min(6.0,  _num(outline_var, 2.0)))
```

注释里写明「上限放到 0.20：原先 min(0.08,...) 会把用户输入压回 0.08，看起来像设置不生效」。**迁移时把这些数字一个一个对过去，不要重写逻辑。**

### 线程模型 — 最容易出事故的地方

现有代码有 **11 处 `root.after(0, ...)`**，全部是后台线程 → 主线程的安全调度：

```
L774  self.root.after(0, self._log_ui, msg)                      # 日志
L796  self.root.after(0, self._add_history_ui, entry)            # 历史
L907  self.root.after(0, lambda: self._ask_player_and_init())    # 文件对话框
L919  self.root.after(0, lambda: self.pause_btn.configure(state="disabled"))
L940  self.root.after(0, self._quit)
L947  self.root.after(0, lambda: self.pause_btn.configure(state="disabled"))
L951  self.root.after(0, lambda: self.pause_btn.configure(text="暂停监听", state="normal"))
L952  self.root.after(0, self.start_monitor)
L455/567/710  win.after(600, win.destroy)                        # 对话框自动关闭
```

`.cursorrules:63` 的硬约束：

> **tkinter 线程安全**：所有后台线程的 UI 更新必须通过 `root.after(0, callback)` 调度到主线程，禁止直接操作控件。

换成 QML 后，`after(0, ...)` 的等价物是 **Qt 信号**：

```python
class UiBridge(QObject):
    logLine      = Signal(str)
    historyAdded = Signal(str, str, str, str)
    pauseState   = Signal(bool)      # 按钮禁用/启用
    requestQuit  = Signal()
```

Python 侧 `QtCore.Signal.emit()` 是线程安全的（跨线程自动排队），QML 侧用 `Connections` 接。

**注意**：`root.after(600, win.destroy)` 这种「保存成功提示 600ms 后关窗」的写法，QML 里用 `Timer { interval: 600; onTriggered: win.close() }`，或者直接用 `ESpringToast` 显示后关闭对话框。

### 配置读写

现有 4 份配置：`sponsor_cfg` / `danmaku_cfg` / `subtitle_cfg` / `sessdata`（`.env`）。

**配置文件的格式、位置、字段名一律不变。** `load_config()` / `save_config()` 的调用方式不要改。你只是在 UI 层把 `StringVar` 换成 QML 的 `property string`，把 `_num()` 钳制后的值写回同一份 dict。

---

## 迁移步骤（按这个顺序做，每步可验证）

### 第 1 步：搭壳，先跑起来一个空窗口

1. 新建 `ui/Main.qml`、`ui/theme.qml`（颜色 token）、`ui/bridge.py`（QObject 桥接）
2. 新建 `bili_yt_player_qml.py` 作为新入口，**暂时不要动 `bili_yt_player.pyw`**
3. 必须这样初始化：

```python
from PySide6.QtQuickControls2 import QQuickStyle

app = QGuiApplication(sys.argv)
QQuickStyle.setStyle("FluentWinUI3")   # 必须早于 engine.load()
engine = QQmlApplicationEngine()
engine.addImportPath(str(ui_dir))
bridge = UiBridge()
engine.rootContext().setContextProperty("bridge", bridge)
engine.load(QUrl.fromLocalFile(str(ui_dir / "Main.qml")))
```

**验收**：空窗口能起来，`FluentWinUI3` 生效（按钮是圆角描边样式）。

### 第 2 步：主窗口三个区块

按 `_build_ui` 的 79 行逐块对应：

| tkinter | QML |
|---|---|
| `ttk.LabelFrame("状态")` + `tk.Text` | `ESpringCard` + `ListView` + `Text` delegate |
| `ttk.LabelFrame("播放历史")` + `ttk.Treeview` | `ESpringCard` + `ListView` + `ESpringListDelegate` |
| `btn_frame` 里 6 个 Button | `ESpringButton` × 6 |
| `sw` 里 3 个 Checkbutton | `ESpringSwitch` × 3 |

日志区域**不要**用 `ECardWithTextArea` 那种 TextArea——那是输入框，有 `placeholderText: "请输入内容"`。必须是 `ListView` + `ListModel`，因为日志是只读追加的。

Treeview 换 ListModel 时注意：

```python
# 现在
self.hist_tree.insert("", "end", values=(t, p, title, detail))

# 之后
bridge.historyAdded.emit(t, p, title, detail)
# QML 侧: histModel.append({time: t, platform: p, title, quality})
```

`_on_history_double_click`（第 818-836 行）里有个关键逻辑：通过 `hist_tree.index(iid)` 拿到行号，再去 `self.history[idx]` 取原始 entry。QML 里 `ListView` 的 delegate 直接有 `index` 和 `model.*`，**更简单了，但 `self.history` 这个 Python 侧列表必须继续维护**——它是重播的数据源，不是纯 UI 状态。

### 第 3 步：4 个设置对话框

按 `_open_danmaku_settings`（最复杂，121 行）开始。8 个数值项用：

```qml
RowLayout {
    Text { text: "不透明度 (0-1)"; Layout.preferredWidth: 140 }
    TextField {
        id: opacityField
        Layout.preferredWidth: 100
        text: cfg.opacity
        validator: DoubleValidator { bottom: 0.05; top: 1.0 }
    }
}
```

**QML 的 `DoubleValidator` 只是软约束**（用户可以粘贴超范围值），所以 `do_save()` 里那套 `max/min` 钳制**必须保留在 Python 侧**。不要以为加了 validator 就能省掉。

`ttk.Combobox` → `ComboBox`（Fluent 样式自动生效）。
`ttk.Checkbutton` → `CheckBox`（表单里用 CheckBox，**不是** ESpringSwitch——Switch 是给"开/关某个功能"用的，CheckBox 是给"多选一组选项"用的）。

### 第 4 步：接线程桥

把 11 处 `root.after(0, ...)` 逐个换成信号 emit。**这里最容易漏**，漏一个就是潜在的跨线程崩溃。

建议做法：`_log` / `_add_history` 这些**已经做了线程安全封装的**函数保持签名不变，只把内部实现从 `after` 换成 `emit`：

```python
def _log(self, msg):                      # 签名不变，调用方全都不用改
    self.logLine.emit(msg)
```

这样 11 处调用点一个都不用动。

### 第 5 步：删旧代码

**只在第 1-4 步全部验收通过后**才做这步：
- 删除 `_build_ui`、4 个 `_open_*_settings`、`_show_sessdata_page`、`_log_ui`、`_add_history_ui` 等纯 UI 方法
- 保留所有业务逻辑方法（`_monitor_loop`、`_worker_loop`、`_process_task`、`_play_bili` 等）
- 保留 `_on_history_double_click` 的业务部分（重播逻辑），只删 UI 部分

`bili_yt_player.pyw` 从 1240 行应该降到 700-800 行左右（纯业务 + 桥接）。

---

## 硬性要求

1. **后端零改动**。`bili_clipboard_dolby.py` / `danmaku.py` / `sponsorblock.py` / `subtitle.py` / `mpv_osc.py` / `osc_base.lua` 一行都不改。
2. **配置文件格式不变**。`%APPDATA%\BiliYTPlayer\` 下的文件、字段名、默认值全部保持。
3. **保留所有范围钳制逻辑**（第 556-575 行那 6 行 `max/min`）。每一条背后都有一个用户实测 bug。
4. **保留 `.cursorrules` 的全部约束**，尤其是：不在 Python 造播放轮子、UI 更新必须回主线程、弹幕字号按分辨率缩放。
5. **不要引入第三方 UI 库**。只用 PySide6 + `ui-kit/euikit/`。
6. **每个 UI 线程调用点都要走信号**。不允许直接从后台线程碰 QML 对象。
7. **打包要能过**。参考 `ui-kit/README.md` 的打包章节，特别是手动过滤 `a.binaries`（不然 exe 150 MB）和不能裁 `Fusion`/`Basic` 样式（不然静默 LOAD FAIL）。

---

## 验收标准

| 项 | 标准 |
|---|---|
| 启动 | `python bili_yt_player_qml.py` 出窗口，Fluent 样式生效 |
| 剪贴板监听 | 复制 B站/YouTube 链接 → 自动播放，日志区出现对应行 |
| 播放历史 | 每次播放追加一行，双击能重播 |
| 3 个开关 | 勾选状态持久化，重启后保留 |
| 4 个设置对话框 | 打开、改值、保存、重开能读到新值 |
| SESSDATA 首次配置 | 未配置时弹窗，输对了能进主界面 |
| 动效 | 按钮按下有缩放、释放有回弹；卡片入场有位移；Toast 能弹出 |
| 深浅色 | 跟随系统，切换正常 |
| exe 体积 | **≤ 50 MB**（超了说明裁错了） |
| Linux | 能在 Linux 上起来（`QQuickStyle` 那句保留，Fluent 在 Linux 上也生效） |

---

## 参考：已经验证过的实现

我在 `%TEMP%\fluent-min\` 留了一套可运行的原型（如果还在的话）：

```
ui/KitDemo.qml                    5 个组件的验收页
ui/euikit/                        组件包（已拷到项目的 ui-kit/euikit/）
run_kit.py                        加载 + 截图
verify_motion.py                  动效时序采样（证明 spring 真的在动）
build_kit.spec                    打包配置（48.79 MB 的实证）
```

`verify_motion.py` 的测法可以复用：QML 的 `pressed` 是只读属性，外部 `setProperty` 不触发 `onPressedChanged`，所以 `ESpringButton` 里留了 `_testPress()` / `_testRelease()` 两个测试钩子和 `_probeScale` 只读属性。**别删它们**，它们是自动化验证动效的唯一入口。

实测弹簧曲线（16ms 采样）：

```
0.96 → 0.9836 → 0.9989 → 1.0010 → 1.0023（过冲） → 1.0
```

---

## 交付物

1. `ui/Main.qml`、`ui/theme.qml`（或内联 token）、各设置对话框 QML
2. `ui/bridge.py`（QObject 桥接）
3. `bili_yt_player_qml.py`（新入口）
4. 改造后的 `bili_yt_player.pyw`（删 UI 层，保留业务层）
5. `build_qml.spec`（打包配置，产出 ≤50 MB）
6. 一份 `MIGRATION.md` 记录：改了哪些方法、删了多少行、遇到什么问题、Linux 上验证结果

**不要**在没有跑通「剪贴板 → 播放 → 历史 → 重播」这条主链路之前，去美化任何一个设置对话框。
