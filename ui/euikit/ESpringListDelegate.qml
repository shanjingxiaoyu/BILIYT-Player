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
    // 悬停色必须**很淡**：ListView 复用 delegate 时 containsMouse 会残留，
    // 实测点过几行后会有多行同时 containsMouse==true。若悬停底色够明显，
    // 用户看到的就是「多行都被选中」（等同 Ctrl+点 的多选观感）。
    // 真正的选中信号只由左侧强调条承担。
    property color hoverColor: Qt.rgba(0, 0, 0, 0.025)
    property color pressedColor: Qt.rgba(0, 0, 0, 0.05)
    property bool isDark: false
    property bool selected: false

    signal doubleClicked()
    signal clicked()

    implicitHeight: rowHeight

    // hover 由外部（ListView）统一判定后传进来。
    //
    // 为什么不用裸的 ma.containsMouse：
    // ListView 复用 delegate 时，实测点过几行后会有**多行同时**
    // containsMouse==true（快速移动 / 复用时 leave 事件不保证送达），
    // 于是多行一起亮，观感就等同于「Ctrl+点击多选」。
    // ListView 层的 indexAt(mouseX, mouseY) 只会命中唯一一行，
    // 把它算出来的行号传下来，逻辑上不可能多行同时 hover。
    property bool hoverManaged: false   // false = 自己用 containsMouse（独立使用时）
    property bool hoveredExternally: false

    readonly property bool hovered: hoverManaged ? hoveredExternally : ma.containsMouse

    Rectangle {
        id: bg
        anchors.fill: parent
        radius: 6
        color: root.selected
               ? (root.isDark ? Qt.rgba(1, 1, 1, 0.13) : Qt.rgba(0, 0, 0, 0.09))
               : (ma.pressed ? root.pressedColor
                             : (root.hovered ? root.hoverColor : "transparent"))

        Behavior on color { ColorAnimation { duration: 110 } }
    }

    // 左侧强调条：**只跟随选中**，不跟随悬停。
    // 这是「看起来像多选」的关键修复 —— 强调条是"选中"的强视觉信号，
    // 如果 hover 也点亮它，鼠标扫过几行就会像同时选中了多行。
    Rectangle {
        id: accent
        width: 3
        height: parent.height - 10
        anchors.left: parent.left
        anchors.leftMargin: 2
        anchors.verticalCenter: parent.verticalCenter
        radius: 1.5
        color: root.accentColor
        opacity: root.selected ? 1 : 0
        scale: root.selected ? 1.0 : 0.2

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
        onClicked: root.clicked()
        onDoubleClicked: root.doubleClicked()
        // 不设置 preventStealing，让 ListView 仍能接管垂直拖拽（滚动）
    }
}
