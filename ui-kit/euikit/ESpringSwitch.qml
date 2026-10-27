import QtQuick
import QtQuick.Controls

// ============================================================
// ESpringSwitch —— Fluent 原生开关 + 弹簧按压手感
//
// 轨道样式、滑块位置、aria 状态全部来自 FluentWinUI3 样式。
// 本组件只叠加按压缩放。
//
// 注意：不要覆盖 Fluent 的 thumb 位移动画 —— 那会和样式
// 内部的 NumberAnimation(167ms OutCubic) 打架，导致滑块抖动。
// 弹簧只作用在 transform 缩放上。
// ============================================================
Switch {
    id: root

    property real pressedScale: 0.94
    property real springStiffness: 2.5
    property real springDamping: 0.25
    property bool motionEnabled: true

    transform: Scale {
        id: scaleTx
        origin.x: root.width / 2
        origin.y: root.height / 2
        xScale: 1.0
        yScale: 1.0
    }

    onPressedChanged: {
        if (!root.motionEnabled) return
        if (root.pressed) {
            scaleTx.xScale = root.pressedScale
            scaleTx.yScale = root.pressedScale
        } else {
            restore.restart()
        }
    }

    ParallelAnimation {
        id: restore
        SpringAnimation {
            target: scaleTx; property: "xScale"; to: 1.0
            spring: root.springStiffness; damping: root.springDamping
        }
        SpringAnimation {
            target: scaleTx; property: "yScale"; to: 1.0
            spring: root.springStiffness; damping: root.springDamping
        }
    }
}
