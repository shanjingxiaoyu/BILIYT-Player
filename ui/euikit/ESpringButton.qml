import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// ============================================================
// ESpringButton —— Fluent 原生外观 + EvolveUI 弹簧手感
//
// 外观、圆角、配色、hover、focus、无障碍全部来自
// QtQuick.Controls 的 FluentWinUI3 样式（QQuickStyle 全局设置）。
// 本组件只额外叠加一层 transform 缩放，不动任何视觉属性。
//
// 弹簧参数抄自 EvolveUI EButton.qml:195-196
//   SpringAnimation { spring: 2.5; damping: 0.25 }
// ============================================================
Button {
    id: root

    // 按下时缩放比例。EvolveUI 原值 0.96
    property real pressedScale: 0.96
    // 弹簧刚度 / 阻尼。越小越弹，damping 越低回弹越明显
    property real springStiffness: 2.5
    property real springDamping: 0.25
    // 关闭动效（无障碍 / 低性能设备）
    property bool motionEnabled: true

    // 探针：供自动化测试读取当前缩放，业务代码不要用
    readonly property real _probeScale: scaleTx.xScale

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
            // 按下直接到位，无过渡 —— 手感要"跟手"
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

    // ---- 自动化测试钩子（业务代码不要调用）----
    // QML 的 Button.pressed 是只读属性，外部 setProperty 不会触发
    // onPressedChanged，所以必须暴露显式入口才能验证动效。
    function _testPress() {
        if (!root.motionEnabled) return
        scaleTx.xScale = root.pressedScale
        scaleTx.yScale = root.pressedScale
    }

    function _testRelease() {
        if (!root.motionEnabled) return
        restore.restart()
    }
}
