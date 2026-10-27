pragma Singleton
import QtQuick
import QtQuick.Controls

// ============================================================
// 设置页专用的设计 token（与 ui/theme.qml 同值）
//
// 为什么不直接 `import ".."` 用 Theme：
//   Lead 启动时 engine.addImportPath(ui/)，而 ui/ 的 qmldir 里注册了
//   `singleton Theme`。此时 ui/settings/*.qml 再写 `import ".."`，同一个
//   ui/ 目录会被 Qt 当成两种东西加载 —— 既是通过 import path 的模块，
//   又是相对目录导入 —— theme.qml 被求值两次，而它 `import QtQuick.Controls`
//   会把 FluentWinUI3 的 Config 单例再初始化一遍，结果是**所有 ComboBox
//   的 implicitHeight/count 归零**（下拉框整个消失，且无任何 QML 警告）。
//
// 实测：去掉 addImportPath 后同一份 QML count=11/implH=32；加上就 count=0/implH=0。
// 这份本地 token 不 import Controls、也不经过 ui/ 的模块解析，绕开双载。
//
// 值必须与 ui/theme.qml 保持一致（那边是 Lead 维护的唯一事实来源）。
// ============================================================
QtObject {
    id: root

    // 跟随系统；截图脚本可通过上下文属性 __forceScheme 强制
    readonly property bool _forced: (typeof __forceScheme !== "undefined") && __forceScheme !== ""
    readonly property bool dark: _forced
        ? (__forceScheme === "dark")
        : (Application.styleHints.colorScheme === Qt.ColorScheme.Dark)

    readonly property color card:      dark ? "#2B2B2B" : "#FFFFFF"
    readonly property color cardAlt:   dark ? "#252525" : "#FAFAFA"
    readonly property color stroke:    dark ? "#3D3D3D" : "#E5E5E5"
    readonly property color text:      dark ? "#FFFFFF" : "#1B1B1B"
    readonly property color text2:     dark ? "#C5C5C5" : "#5C5C5C"
    readonly property color text3:     dark ? "#8A8A8A" : "#8A8A8A"
    readonly property color accent:    dark ? "#4CC2FF" : "#0078D4"
    readonly property color hover:     dark ? Qt.rgba(1, 1, 1, 0.06) : Qt.rgba(0, 0, 0, 0.05)
    readonly property color sel:       dark ? Qt.rgba(1, 1, 1, 0.10) : Qt.rgba(0, 0, 0, 0.07)
    readonly property color windowBg:  dark ? "#202020" : "#F3F3F3"
    readonly property color danger:    dark ? "#FF99A4" : "#C42B1C"
    readonly property color ok:        dark ? "#6CCB5F" : "#107C10"

    readonly property int radius:      8
    readonly property int radiusCard:  12
    readonly property int gap:         12
    readonly property int pageMaxW:    920
    readonly property int titleSize:   15
    readonly property int bodySize:    13
}
