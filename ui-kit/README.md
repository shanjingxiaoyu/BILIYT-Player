# EUIKit — Fluent 原生外观 + EvolveUI 弹簧手感

> 5 个 QML 组件，约 12 KB 源码。外观全部来自 PySide6 内置的 `FluentWinUI3` 样式（Qt 官方实现 Fluent 2 规范），动效参数抄自 [EvolveUI](https://github.com/sudoevolve/EvolveUI) 的 `EButton` / `ESwitchButton`。

---

## 为什么是这个组合

| 方案 | 体积 | 动效 | 问题 |
|---|---|---|---|
| tkinter（现状） | 12 MB | 无 | 没有动效能力 |
| PySide6 + Fluent 内建 | 48.8 MB | 克制（0 处 spring） | 微软设计哲学不弹 |
| PySide6 + EvolveUI 全套 | 56.3 MB | 强（28 处 spring） | 第三方个人项目，需带 3.7 MB 字体，42 个组件 |
| **PySide6 + EUIKit** | **≈48.8 MB** | **强** | **本方案** |

EvolveUI 真正"灵动"的核心只有两行：

```qml
SpringAnimation { spring: 2.5; damping: 0.25 }
```

其余 40 个组件、3.7 MB 字体、9 处 qrc 路径修复都是配套成本。EUIKit 只取这两行的思想，套在 Fluent 控件上——**既拿到 WinUI3 原生观感和无障碍，又有弹簧手感，体积和纯 Fluent 方案一样。**

---

## 安装

把 `euikit/` 整个目录拷到你的 QML 目录下，然后：

```qml
import "euikit"
```

Python 侧**必须**在任何 QML 加载之前设置样式：

```python
from PySide6.QtQuickControls2 import QQuickStyle
QQuickStyle.setStyle("FluentWinUI3")   # 必须在 QGuiApplication 之后、load() 之前
```

`qmldir` 已注册 5 个组件，不需要额外 import 语句。

---

## 组件清单

### 1. `ESpringButton` — 弹簧按钮

```qml
import "euikit"

ESpringButton {
    text: "暂停监听"
    onClicked: console.log("clicked")
}
```

| 属性 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `pressedScale` | real | `0.96` | 按下缩放比例 |
| `springStiffness` | real | `2.5` | 弹簧刚度。越小越软 |
| `springDamping` | real | `0.25` | 阻尼。越低回弹越明显 |
| `motionEnabled` | bool | `true` | `false` = 关闭动效 |

**实测曲线**（16ms 采样）：

```
0.96 → 0.9836 → 0.9989 → 1.0010 → 1.0023（过冲） → 1.0
```

按下立即到 0.96（跟手），释放后 spring 回弹并轻微过冲——这是"有重量感"的来源。

**外观**：圆角、描边、hover 底色、focus 环、键盘导航全部由 Fluent 样式提供，本组件只管 `transform` 缩放。

---

### 2. `ESpringSwitch` — 弹簧开关

```qml
ESpringSwitch {
    text: "自动跳过广告"
    checked: true
    onToggled: console.log(checked)
}
```

| 属性 | 默认 | 说明 |
|---|---|---|
| `pressedScale` | `0.94` | 按下缩放 |
| `springStiffness` / `springDamping` | `2.5` / `0.25` | 同上 |
| `motionEnabled` | `true` | 动效开关 |

**注意**：轨道和滑块的位移、颜色由 Fluent 样式内部驱动（167ms `OutCubic`）。**不要覆盖 `indicator` 或 `contentItem`**，会和样式内部动画打架导致滑块抖动。弹簧只作用在整体 `transform` 上。

---

### 3. `ESpringCard` — 卡片容器

替代 tkinter 的 `ttk.LabelFrame`。

```qml
ESpringCard {
    Layout.fillWidth: true
    Layout.preferredHeight: 220

    Text { text: "状态"; font.bold: true }
    // 直接往里面放子项，ColumnLayout 自动竖排
}
```

| 属性 | 默认 | 说明 |
|---|---|---|
| `radius` | `12` | Fluent 2 规范：4 / 8 / 12 |
| `cardColor` / `borderColor` | `#FFFFFF` / `#E5E5E5` | 深浅色要自己传 |
| `padding` | `14` | 内边距 |
| `hoverLift` | `true` | 悬停上浮 2px |
| `entrance` | `true` | 入场动画（opacity + y 偏移） |
| `clickable` | `false` | 设为 `true` 则可点击 + 按压缩放 |
| `signal clicked()` | — | `clickable: true` 时触发 |

入场用 `y: 12 → 0` 位移而非 scale，避免文字发虚。

---

### 4. `ESpringListDelegate` — 列表行

替代 `ttk.Treeview`。**用法差异要注意**：

| tkinter | QML |
|---|---|
| `tv.insert("", "end", values=(a,b,c))` | `model.append({k1:a, k2:b})` |
| `tv.bind("<Double-1>", h)` | `onDoubleClicked: h()` |
| `tv.delete(*tv.get_children())` | `model.clear()` |

```qml
ListView {
    model: ListModel { id: m }

    delegate: ESpringListDelegate {
        width: ListView.view.width
        accentColor: "#0078D4"
        selected: ListView.isCurrentItem

        // 行内容自己排
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 12
            Text { text: model.time }
            Text { text: model.title; Layout.fillWidth: true; elide: Text.ElideRight }
        }

        onDoubleClicked: replay(model.title)
    }
}
```

| 属性 | 默认 | 说明 |
|---|---|---|
| `rowHeight` | `36` | 行高 |
| `accentColor` | `#0078D4` | 左侧强调条颜色 |
| `selected` | `false` | 绑 `ListView.isCurrentItem` |
| `signal doubleClicked()` | — | 双击回调 |

左强调条用 `Behavior on scale` + `SpringAnimation` 从 0.2 弹到 1.0。

---

### 5. `ESpringToast` — 轻提示

替代"把反馈写进日志"的做法。

```qml
ESpringToast { id: toast; anchors.fill: parent }

// 调用
toast.show("已清空历史")
```

| 属性 | 默认 | 说明 |
|---|---|---|
| `maxWidth` | `320` | 最大宽度 |
| `bgColor` / `textColor` | `#2B2B2B` / `#FFFFFF` | 配色 |
| `autoHideMs` | `2200` | 自动消失时间 |

`show(msg)` 弹出，`scale 0.9 → 1.0` 走 spring（有轻微过冲）+ opacity 淡入。

---

## 颜色：深浅色要自己传

Fluent 样式负责**控件**的深浅色，但**自定义容器**（`ESpringCard`、`ESpringListDelegate`）需要你自己传色。推荐在根窗口定义一份：

```qml
ApplicationWindow {
    property bool dark: Application.styleHints.colorScheme === Qt.Dark

    property var c: dark ? {
        card: "#2B2B2B", stroke: "#3D3D3D", text: "#FFFFFF",
        text2: "#C5C5C5", accent: "#4CC2FF", hover: Qt.rgba(1,1,1,0.06)
    } : {
        card: "#FFFFFF", stroke: "#E5E5E5", text: "#1B1B1B",
        text2: "#5C5C5C", accent: "#0078D4", hover: Qt.rgba(0,0,0,0.05)
    }
}
```

这组值取自 Fluent 2 规范（`fluent2.microsoft.design` 的 neutral / accent 色阶）。

---

## 调参指南

想要更弹 / 更稳，只调这两个数：

| 目标 | spring | damping |
|---|---|---|
| 当前默认（推荐） | 2.5 | 0.25 |
| 更弹、更"玩具感" | 3.5 | 0.18 |
| 更稳、更商务 | 2.0 | 0.35 |
| 几乎不弹（只留跟手感） | 6.0 | 0.9 |

`spring` 越小越软，`damping` 越低回弹越久。**两个一起调**，只改一个容易变成"过冲过头"或"根本没动"。

---

## 无障碍

```qml
// 系统开了"减少动态效果"时自动关闭所有动效
ESpringButton {
    motionEnabled: !Settings.reduceMotion
}
```

Qt 6.9 可以通过 `Application.styleHints` 读系统设置。**上线前务必接上这一条**——这是 Fluent 规范的要求，也是 5 个组件的 `motionEnabled` 存在的理由。

---

## 打包注意事项（实测踩过的坑）

### 必须手动过滤 binaries

PySide6 的 PyInstaller hook 会**无差别收集全部 Qt DLL**，`excludes` 对二进制无效：

```python
a = Analysis(...)          # 这里 excludes 只影响 Python 模块
_DROP = ["Qt6WebEngine", "opengl32sw", "Qt6Pdf", "Qt6Quick3D", ...]
a.binaries = [(d,s,t) for (d,s,t) in a.binaries
              if not any(x.lower() in d.lower() for x in _DROP)]
```

不做这一步，`Qt6WebEngineCore.dll` 单独 190 MB 会一起进去，exe 直接 150 MB。

### 两个不能裁的样式

```
FluentWinUI3/qmldir 第 5 行:  import QtQuick.Controls.Fusion auto
```

- `Fusion` **不能裁** → 裁了运行时报 `LOAD FAIL`（静默，无有用错误信息）
- `Basic` **不能裁** → Fluent 控件回退到 Basic 实现

可以裁：`Material` / `Universal` / `Imagine` / `Windows`。

### 实测体积

| 配置 | exe |
|---|---|
| 不裁剪 | 150.21 MB |
| 只裁 WebEngine + Pdf | 64.51 MB |
| **裁 WebEngine + Pdf + 3D + Charts + 未用样式** | **≈48.8 MB** |

---

## 许可

| 来源 | 许可 | 说明 |
|---|---|---|
| `FluentWinUI3` 样式 | **LGPL-3.0 / GPL / Qt 商业** | 随 PySide6 分发。LGPL 允许闭源分发，要求动态链接且用户可替换 Qt 库 |
| EvolveUI 的 spring 参数 | **MIT** | 只借用了 `spring: 2.5; damping: 0.25` 两个数值和思路，无代码复制 |
| 本组件包源码 | 你自己定 | 5 个文件约 12 KB，独立实现 |

---

## 未做的事（有意留白）

- **没有 XAML 数据绑定**：这是 QML，用 `property` + `model`，比 WinUI 的 `x:Bind` 简单但功能少
- **没有做动画编排**：组件级动效够了，如果要做窗口级/页面级转场，再补 `EUIKitTransition`
- **没有接系统 reduce-motion**：留了 `motionEnabled` 开关，接线要你按项目实际读系统设置的路径做
- **没有 Mica / Acrylic 背景**：Fluent 2 的材质效果需要 Win32 API，QML 侧只能用 `MultiEffect` 模拟，效果打折，所以没做
