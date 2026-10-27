import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../euikit"

// 广告跳过 —— sponsorblock.conf
//
// 11 个分类 × 四档（auto / manual / bar / prohibited）+ 进度条颜色块。
// 颜色来自 CATEGORY_COLORS 的 ASS `&H00BBGGRR&`，bridge 已反解成 #RRGGBB。
SettingsPage {
    id: page

    // [{key,name,desc,color,tier}] —— 顺序即 ALL_CATEGORIES 顺序
    property var cats: []
    // {category: tier} 当前选择
    property var tiers: ({})

    title: "广告跳过"
    subtitle: "「自动跳过」= 进入片段立即跳，撤销窗口内可按 Enter 撤销；「手动跳过」= 进度条显色，按 n 跳过。保存后播放下一个视频即生效。"

    function load() {
        if (!page.bridge) return
        var d = page.bridge.pageData("sponsor")
        if (!d || !d.categories) return
        page.cats = d.categories
        var t = ({})
        for (var i = 0; i < d.categories.length; i++) {
            var c = d.categories[i]
            t[c.key] = c.tier
        }
        page.tiers = t
        notifyBox.checked = d.notify !== false
        undoField.text = fmt(d.undo_seconds, 3.0)
        minField.text = fmt(d.min_duration, 1.0)
        catModel.clear()
        for (var j = 0; j < d.categories.length; j++) {
            catModel.append({
                cat: d.categories[j].key,
                name: d.categories[j].name,
                desc: d.categories[j].desc,
                swatch: d.categories[j].color,
                tier: d.categories[j].tier
            })
        }
    }

    function fmt(v, dflt) {
        var n = Number(v)
        if (!isFinite(n)) n = dflt
        return String(Math.round(n * 10000) / 10000)
    }

    Component.onCompleted: load()

    ESpringCard {
        Layout.fillWidth: true
        cardColor: SettingsTheme.card
        borderColor: SettingsTheme.stroke

        Text {
            Layout.fillWidth: true
            text: "片段类别与跳过方式"
            color: SettingsTheme.text
            font.pixelSize: SettingsTheme.bodySize
            font.weight: Font.DemiBold
        }

        // 表头
        RowLayout {
            Layout.fillWidth: true
            Layout.topMargin: 2
            spacing: 12

            Text {
                Layout.preferredWidth: 190
                text: "类别"
                color: SettingsTheme.text2
                font.pixelSize: 11
                font.weight: Font.DemiBold
            }
            Text {
                Layout.preferredWidth: 170
                text: "跳过方式"
                color: SettingsTheme.text2
                font.pixelSize: 11
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                text: "进度条颜色 / 说明"
                color: SettingsTheme.text2
                font.pixelSize: 11
                font.weight: Font.DemiBold
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 1
            color: SettingsTheme.stroke
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 6

            Repeater {
                model: ListModel { id: catModel }

                delegate: RowLayout {
                    id: catRow
                    required property string cat
                    required property string name
                    required property string desc
                    required property string swatch
                    required property string tier

                    Layout.fillWidth: true
                    spacing: 12

                    Text {
                        Layout.preferredWidth: 190
                        text: catRow.name
                        color: SettingsTheme.text
                        font.pixelSize: SettingsTheme.bodySize
                        elide: Text.ElideRight
                    }

                    SettingsComboBox {
                        Layout.preferredWidth: 170
                        textRole: "label"
                        valueRole: "key"
                        model: page.meta && page.meta.tiers ? page.meta.tiers : []
                        Component.onCompleted: {
                            for (var i = 0; i < model.length; i++)
                                if (model[i].key === catRow.tier) { currentIndex = i; break }
                        }
                        onActivated: page.tiers[catRow.cat] = model[currentIndex].key
                    }

                    Rectangle {          // 进度条颜色块
                        Layout.preferredWidth: 20
                        Layout.preferredHeight: 20
                        radius: 4
                        color: catRow.swatch
                        border.color: SettingsTheme.stroke
                        border.width: 1
                    }

                    Text {
                        Layout.fillWidth: true
                        text: catRow.desc
                        color: SettingsTheme.text3
                        font.pixelSize: 11
                        elide: Text.ElideRight
                    }
                }
            }
        }
    }

    ESpringCard {
        Layout.fillWidth: true
        cardColor: SettingsTheme.card
        borderColor: SettingsTheme.stroke

        Text {
            Layout.fillWidth: true
            text: "选项"
            color: SettingsTheme.text
            font.pixelSize: SettingsTheme.bodySize
            font.weight: Font.DemiBold
        }

        CheckBox {
            id: notifyBox
            objectName: "notifyBox"
            text: "跳过时显示提示（含 Enter 撤销倒计时）"
            checked: true
        }

        SettingRow {
            Layout.fillWidth: true
            label: "撤销窗口（秒）"
            hint: "≥ 0，自动跳过后可按 Enter 撤销的时长"
            TextField {
                id: undoField
                objectName: "undoField"
                Layout.preferredWidth: 110
                validator: DoubleValidator { bottom: 0; top: 60; decimals: 2 }
            }
        }

        SettingRow {
            Layout.fillWidth: true
            label: "最短片段（秒）"
            hint: "≥ 0，短于此长度的片段忽略（防抖动）"
            TextField {
                id: minField
                objectName: "minField"
                Layout.preferredWidth: 110
                validator: DoubleValidator { bottom: 0; top: 60; decimals: 2 }
            }
        }
    }

    // 保存入口（PageActions 与自检脚本都走这里）
    function save() {
        if (!page.bridge) return
        var list = []
        for (var i = 0; i < catModel.count; i++) {
            var row = catModel.get(i)
            var t = page.tiers[row.cat]
            list.push({ key: row.cat, tier: t ? t : row.tier })
        }
        page.report(page.bridge.saveSponsor({
            categories: list,
            notify: notifyBox.checked,
            undo_seconds: undoField.text,
            min_duration: minField.text
        }))
    }

    PageActions {
        Layout.fillWidth: true
        primaryText: "保存"
        status: page.statusText
        statusOk: page.statusOk
        onPrimaryClicked: page.save()
    }
}
