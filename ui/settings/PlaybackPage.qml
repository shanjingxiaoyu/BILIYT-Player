import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../euikit"

// 画质 —— general.conf 的 quality（上限档位）
// 选项来自 app/quality.py 的 QUALITY_OPTIONS（可能尚未就绪 → bridge 已兜底）
SettingsPage {
    id: page

    title: "画质"
    subtitle: "限制 B 站视频的最高画质档位。实际能拿到什么还取决于视频本身与账号权限（大会员）。"

    function load() {
        if (!page.bridge) return
        var d = page.bridge.pageData("general")
        page.cfg = d ? d : {}
    }

    Component.onCompleted: load()

    ESpringCard {
        Layout.fillWidth: true
        cardColor: SettingsTheme.card
        borderColor: SettingsTheme.stroke

        Text {
            Layout.fillWidth: true
            text: "画质上限"
            color: SettingsTheme.text
            font.pixelSize: SettingsTheme.bodySize
            font.weight: Font.DemiBold
        }

        SettingRow {
            Layout.fillWidth: true
            label: "最高档位"
            hint: "「自动」= 请求账号能拿到的最高画质"

            SettingsComboBox {
                id: qualityCombo
                objectName: "qualityCombo"
                Layout.preferredWidth: 260
                textRole: "label"
                valueRole: "key"
                model: page.meta && page.meta.quality ? page.meta.quality : []
                currentIndex: {
                    var want = page.cfg.quality || "auto"
                    for (var i = 0; i < model.length; i++)
                        if (model[i].key === want) return i
                    return 0
                }
                // ComboBox 自带的 currentValue 就是 valueRole 取到的 key
                function quality() { return currentValue !== undefined ? currentValue : "auto" }
            }
        }

        Text {
            Layout.fillWidth: true
            text: "选低于视频实际画质的档位时，实际播放会降到该档位；选高于视频的档位不会报错，只会回落到最好那份。"
            color: SettingsTheme.text3
            font.pixelSize: 11
            wrapMode: Text.WordWrap
        }
    }

    // 保存入口（PageActions 与自检脚本都走这里）
    function save() {
        if (!page.bridge) return
        page.report(page.bridge.saveGeneral({ quality: qualityCombo.quality() }))
    }

    PageActions {
        Layout.fillWidth: true
        primaryText: "保存"
        status: page.statusText
        statusOk: page.statusOk
        onPrimaryClicked: page.save()
    }
}
