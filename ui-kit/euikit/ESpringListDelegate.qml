import QtQuick
import QtQuick.Controls

// ============================================================
// ESpringListDelegate —— 列表行 + 左侧强调条 + 悬停/按压手感
//
// 用于替代 tkinter 的 ttk.Treeview：
//   tv.insert("", "end", values=(...))  →  model.append({...})
//   tv.bind("<Double-1>", handler)      →  onDoubleClicked
//
// 左强调条用 Behavior on scale + SpringAnimation（Fluent 原本只是淡入）。
// ============================================================
Item {
    id: root

    property int rowHeight: 36
    property string accentColor: "#0078D4"
    property color textColor: "#1B1B1B"
    property color textDimColor: "#5C5C5C"
    property color hoverColor: Qt.rgba(0, 0, 0, 0.05)
    property color pressedColor: Qt.rgba(0, 0, 0, 0.09)
    property bool isDark: false
    property bool selected: false

    signal doubleClicked()

    implicitHeight: rowHeight

    readonly property bool _active: ma.containsMouse || root.selected

    Rectangle {
        id: bg
        anchors.fill: parent
        radius: 6
        color: root.selected
               ? Qt.rgba(0, 0, 0, root.isDark ? 0.22 : 0.07)
               : (ma.pressed ? root.pressedColor
                             : (ma.containsMouse ? root.hoverColor : "transparent"))

        Behavior on color { ColorAnimation { duration: 110 } }
    }

    // 左侧强调条：声明式绑定 + Spring 补间
    Rectangle {
        id: accent
        width: 3
        height: parent.height - 10
        anchors.left: parent.left
        anchors.leftMargin: 2
        anchors.verticalCenter: parent.verticalCenter
        radius: 1.5
        color: root.accentColor
        opacity: root._active ? 1 : 0
        scale: root._active ? 1.0 : 0.2

        Behavior on opacity { NumberAnimation { duration: 120 } }
        Behavior on scale {
            SpringAnimation { spring: 4.0; damping: 0.3 }
        }
    }

    MouseArea {
        id: ma
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onDoubleClicked: root.doubleClicked()
    }
}
