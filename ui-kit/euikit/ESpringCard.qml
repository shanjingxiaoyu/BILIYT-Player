import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// ============================================================
// ESpringCard —— Fluent Card + 入场/悬停/按压微动效
//
// Fluent 规范里卡片是静态的（只有 border/shadow 变化）。
// 这个组件补三件事：
//   1. 入场：opacity 0→1 + y 偏移 12→0（spring，一次性）
//   2. 悬停：上浮 -2px（spring，可关）
//   3. 按压：缩放 0.98（可选，给可点击卡片用）
//
// 视觉属性（底色/边框/圆角）默认值贴近 Fluent 2 规范。
// ============================================================
Item {
    id: root

    // ---- 外观（默认值贴近 Fluent 2）----
    property int radius: 12                 // Fluent 2: 4 / 8 / 12
    property color cardColor: "#FFFFFF"
    property color borderColor: "#E5E5E5"
    property int borderWidth: 1
    property int padding: 14

    // ---- 动效开关 ----
    property bool hoverLift: true
    property bool entrance: true
    property bool clickable: false
    property real pressedScale: 0.98
    property real springStiffness: 2.5
    property real springDamping: 0.25

    // ---- 插槽 ----
    default property alias content: contentLayout.data

    signal clicked()

    implicitWidth: contentLayout.implicitWidth + padding * 2
    implicitHeight: contentLayout.implicitHeight + padding * 2
    opacity: root.entrance ? 0 : 1

    Rectangle {
        id: bg
        anchors.fill: parent
        radius: root.radius
        color: root.cardColor
        border.color: root.borderColor
        border.width: root.borderWidth

        Behavior on border.color { ColorAnimation { duration: 150 } }
    }

    ColumnLayout {
        id: contentLayout
        anchors.fill: parent
        anchors.margins: root.padding
        spacing: 8
    }

    // 关键：多个 transform 必须放进数组，不能声明两次 transform
    transform: [
        Translate {
            id: offsetTx
            x: 0
            y: root.entrance ? 12 : 0
        },
        Scale {
            id: pressTx
            origin.x: root.width / 2
            origin.y: root.height / 2
            xScale: 1.0
            yScale: 1.0
        }
    ]

    MouseArea {
        id: hoverArea
        anchors.fill: parent
        hoverEnabled: root.hoverLift || root.clickable
        cursorShape: root.clickable ? Qt.PointingHandCursor : Qt.ArrowCursor
        acceptedButtons: root.clickable ? Qt.LeftButton : Qt.NoButton

        onEntered: if (root.hoverLift) { liftAnim.to = -2; liftAnim.restart() }
        onExited: if (root.hoverLift) { liftAnim.to = 0; liftAnim.restart() }
        onPressed: if (root.clickable) pressTx.yScale = root.pressedScale
        onReleased: {
            if (root.clickable) { restorePress.restart(); root.clicked() }
        }
        onCanceled: if (root.clickable) restorePress.restart()
    }

    // ---- 悬停上浮：用 y 偏移而非 scale，避免文字发虚 ----
    SpringAnimation {
        id: liftAnim
        target: offsetTx; property: "y"
        spring: 3.0; damping: 0.35
    }

    ParallelAnimation {
        id: restorePress
        SpringAnimation {
            target: pressTx; property: "xScale"; to: 1.0
            spring: root.springStiffness; damping: root.springDamping
        }
        SpringAnimation {
            target: pressTx; property: "yScale"; to: 1.0
            spring: root.springStiffness; damping: root.springDamping
        }
    }

    // ---- 入场 ----
    NumberAnimation {
        id: entranceAnim
        target: root; property: "opacity"
        from: 0; to: 1
        duration: 220
        easing.type: Easing.OutCubic
    }

    Component.onCompleted: {
        if (!root.entrance) return
        entranceAnim.start()
        liftAnim.to = 0
        liftAnim.restart()
    }
}
