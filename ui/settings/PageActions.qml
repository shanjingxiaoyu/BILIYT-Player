import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../euikit"

// PageActions —— 页底动作条：状态文字（左）+ 主/次按钮 + 可选清缓存
//
// 保存反馈**不**自动关窗（这是设置页，不是对话框），所以状态文字常驻到下一次操作。
RowLayout {
    id: root

    property string status: ""
    property bool statusOk: true
    property string primaryText: "保存"
    property string secondaryText: ""
    property bool showClearCache: false

    signal primaryClicked()
    signal secondaryClicked()
    signal clearCacheClicked()

    Layout.topMargin: 4
    spacing: 8

    Text {
        Layout.fillWidth: true
        Layout.alignment: Qt.AlignVCenter
        text: root.status
        color: root.statusOk ? SettingsTheme.ok : SettingsTheme.danger
        font.pixelSize: 12
        wrapMode: Text.WordWrap
        maximumLineCount: 2
        elide: Text.ElideRight
    }

    ESpringButton {
        visible: root.showClearCache
        text: "清空弹幕缓存"
        onClicked: root.clearCacheClicked()
    }

    ESpringButton {
        visible: root.secondaryText !== ""
        text: root.secondaryText
        onClicked: root.secondaryClicked()
    }

    ESpringButton {
        text: root.primaryText
        onClicked: root.primaryClicked()
    }
}
