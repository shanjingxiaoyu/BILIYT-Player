import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// ============================================================
// SettingRow —— 一行设置项：「标签(+提示) | 控件」
//
// Fluent 设置页的标准行布局。同一块「固定宽标签 + 自适应控件」在 6 个
// 页签里出现 30 多次，所以抽成一个组件。
//
// 用法：
//   SettingRow {
//       label: "不透明度 (0-1)"
//       hint: "0.05 ~ 1.0"
//       TextField { Layout.preferredWidth: 110 }
//   }
//
// 根元素就是 RowLayout，子项直接当它的 children —— 不用 default alias
// 转发（转发会让栈式布局拿不到子项的 implicitHeight，实测高度塌成 0）。
// ============================================================
RowLayout {
    id: root

    property string label: ""
    property string hint: ""
    property int labelWidth: 170

    spacing: 12

    ColumnLayout {
        Layout.preferredWidth: root.labelWidth
        Layout.maximumWidth: root.labelWidth
        Layout.fillWidth: false      // 布局类子项默认 fillWidth，不关会吃满整行
        Layout.alignment: Qt.AlignVCenter
        spacing: 1

        Text {
            Layout.fillWidth: true
            text: root.label
            color: SettingsTheme.text
            font.pixelSize: SettingsTheme.bodySize
            elide: Text.ElideRight
        }
        Text {
            Layout.fillWidth: true
            visible: root.hint !== ""
            text: root.hint
            color: SettingsTheme.text3
            font.pixelSize: 11
            wrapMode: Text.WordWrap
        }
    }
}
