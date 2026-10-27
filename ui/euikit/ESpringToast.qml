import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Effects

// ============================================================
// ESpringToast —— 轻提示，右下角滑入 + spring 超出回弹
//
// tkinter 版本没有 toast（只有 tk.Text 里插日志行）。
// QML 侧用它替代"操作反馈写进日志"的做法。
//
// 用法：
//   ESpringToast { id: toast; anchors.fill: parent }
//   toast.show("已清空历史")
// ============================================================
Item {
    id: root

    property int maxWidth: 320
    property color bgColor: "#2B2B2B"
    property color textColor: "#FFFFFF"
    property int autoHideMs: 2200

    // 当前提示
    property string message: ""

    // 不阻塞鼠标
    z: 9999

    function show(msg) {
        root.message = msg
        root.visible = true
        box.popup()
        hideTimer.restart()
    }

    visible: false

    Timer {
        id: hideTimer
        interval: root.autoHideMs
        onTriggered: box.dismiss()
    }

    Rectangle {
        id: box
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.rightMargin: 20
        anchors.bottomMargin: 20

        width: Math.min(root.maxWidth, txt.implicitWidth + 32)
        height: txt.implicitHeight + 22
        radius: 8
        color: root.bgColor
        opacity: 0
        scale: 0.9

        function popup() {
            showAnim.restart()
        }

        function dismiss() {
            hideAnim.start()
        }

        Text {
            id: txt
            anchors.centerIn: parent
            width: Math.min(root.maxWidth - 32, implicitWidth)
            text: root.message
            color: root.textColor
            font.pixelSize: 13
            wrapMode: Text.WordWrap
        }

        // 滑入：从下往上 + spring 轻微过冲
        ParallelAnimation {
            id: showAnim
            NumberAnimation {
                target: box; property: "opacity"
                from: 0; to: 1
                duration: 140
            }
            SpringAnimation {
                target: box; property: "scale"
                from: 0.9; to: 1.0
                spring: 3.2; damping: 0.24
            }
        }

        NumberAnimation {
            id: hideAnim
            target: box; property: "opacity"
            from: 1; to: 0
            duration: 160
            easing.type: Easing.InCubic
            onFinished: root.visible = false
        }
    }
}
