import QtQuick
import QtQuick.Controls

// ============================================================
// SettingsComboBox —— 设置页统一下拉框
//
// 为什么不直接用 ComboBox：
//   Fluent 深色主题里 ComboBox 的填充是 #373737 左右（实测 (55,55,55)），
//   而卡片底色是 #2B2B2B (43,43,43)。对比度只有约 1.2:1 —— 用户实测反馈
//   「深色模式下选项框发灰」，边界看不出在哪儿。
//   原生就是这个色（我和纯 Fluent 深色裸 ComboBox 逐像素比过），不是 bug，
//   但既然用户觉得看不清，就把它调成「明显一点、又不刺眼」：
//     深色：#2B2B2B 底 + #5A5A5A 描边   （与卡片同底，靠描边分界）
//     浅色：#FAFAFA 底 + #D6D6D6 描边
//   只覆盖 background，contentItem / indicator / popup 全部留给 Fluent，
//   不改样式内部实现（改了会和它自己的状态动画打架）。
// ============================================================
ComboBox {
    id: control

    implicitWidth: Math.max(200, implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: 32

    leftPadding: 10
    rightPadding: 10

    background: Rectangle {
        radius: 4
        color: control.hovered
               ? (SettingsTheme.dark ? "#333333" : "#F0F0F0")
               : (SettingsTheme.dark ? "#2B2B2B" : "#FAFAFA")
        border.width: 1
        border.color: control.activeFocus || control.popup.visible
                      ? SettingsTheme.accent
                      : (SettingsTheme.dark ? "#5A5A5A" : "#D6D6D6")

        Behavior on border.color { ColorAnimation { duration: 120 } }
        Behavior on color { ColorAnimation { duration: 120 } }
    }

    contentItem: Text {
        leftPadding: control.leftPadding
        rightPadding: 26          // 给右侧箭头留位
        text: control.displayText
        font: control.font
        color: SettingsTheme.text
        elide: Text.ElideRight
        verticalAlignment: Text.AlignVCenter
        horizontalAlignment: Text.AlignLeft
    }

    // Fluent 的箭头图是给默认内边距设计的，这里自己重画成矢量，任何底色都清楚
    indicator: Canvas {
        x: control.width - width - 11
        y: (control.height - height) / 2 + 1
        width: 11
        height: 7

        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            ctx.strokeStyle = SettingsTheme.dark ? "#C5C5C5" : "#5C5C5C"
            ctx.lineWidth = 1.5
            ctx.lineCap = "round"
            ctx.lineJoin = "round"
            ctx.beginPath()
            ctx.moveTo(1, 1)
            ctx.lineTo(width / 2, height - 1)
            ctx.lineTo(width - 1, 1)
            ctx.stroke()
        }

        // 主题切换时重画（Canvas 不会自动跟随 color 变化）
        Connections {
            target: SettingsTheme
            function onDarkChanged() { indicator.requestPaint() }
        }
    }
}
