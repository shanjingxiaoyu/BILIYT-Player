import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// ============================================================
// SettingsPage —— 所有设置页的公共外框
//
// 职责：
//   * 标题 / 说明 / 可滚动内容（宽窗口下限制行长，与 Fluent 设置页一致）
//   * 保存结果回显字段（statusText / statusOk）与 saved 信号
//   * bridge / meta 的传递
//
// 子页用默认属性追加内容，排在标题下方（与 ESpringCard 同一模式）：
//   SettingsPage {
//       title: "弹幕"
//       ESpringCard { ... }
//   }
// ============================================================
Flickable {
    id: root

    property string title: ""
    property string subtitle: ""

    // 由 SettingsWindow 注入
    property var bridge: null
    property var meta: ({})
    property var cfg: ({})

    // 保存结果（PageActions 直接读这两个）
    property string statusText: ""
    property bool statusOk: true

    // 保存成功时 emit，SettingsWindow 接住弹 ESpringToast
    signal saved(string msg)

    default property alias content: column.data

    clip: true
    flickableDirection: Flickable.VerticalFlick
    contentWidth: width
    contentHeight: column.implicitHeight + 40
    boundsBehavior: Flickable.StopAtBounds
    ScrollBar.vertical: ScrollBar {}

    // 子页覆盖：从 bridge 重读 conf 回填控件
    function load() {}

    // 子页保存后统一走这里：回显 + 成功提示 + 重新载入
    function report(r) {
        var ok = !!(r && r.ok)
        root.statusOk = ok
        root.statusText = (r && r.msg) ? String(r.msg) : (ok ? "已保存" : "保存失败")
        if (ok) {
            root.load()
            root.saved(root.statusText)
        }
    }

    onVisibleChanged: if (visible) root.load()

    ColumnLayout {
        id: column
        width: Math.min(root.width - 48, SettingsTheme.pageMaxW)
        x: Math.max(24, (root.width - width) / 2)
        y: 22
        spacing: 10

        Text {
            Layout.fillWidth: true
            text: root.title
            color: SettingsTheme.text
            font.pixelSize: 20
            font.weight: Font.DemiBold
        }

        Text {
            Layout.fillWidth: true
            Layout.bottomMargin: 6
            visible: root.subtitle !== ""
            text: root.subtitle
            color: SettingsTheme.text2
            font.pixelSize: 12
            wrapMode: Text.WordWrap
        }
    }
}
