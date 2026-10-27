pragma Singleton
import QtQuick
import QtQuick.Controls

// Fluent 2 设计 token（neutral / accent 色阶，取自 fluent2.microsoft.design）
// 用法: import ".."  →  Theme.card / Theme.stroke / Theme.accent ...
//
// 深浅色默认跟随系统（Qt 6.5+ Application.styleHints.colorScheme）。
// 截图脚本可通过上下文属性 __forceScheme = "light"/"dark" 强制指定。
QtObject {
    id: root

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
