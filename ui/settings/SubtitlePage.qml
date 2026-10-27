import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../euikit"

// 字幕 —— subtitle.conf
//
// 仅控制 B 站提供的字幕；UP 主压制在画面里的硬字幕无法调整（与 legacy 文案一致）。
// 范围钳制在 ui/settings_bridge.py：position_ratio 0~0.4 / font_size_ratio
// 0.01~0.12 / opacity 0.05~1.0 / outline 0~6。
SettingsPage {
    id: page

    title: "字幕"
    subtitle: "只控制 B 站提供的原生字幕；UP 主压制在画面里的硬字幕无法调整。保存后播放下一个视频即生效。"

    function load() {
        if (!page.bridge) return
        var d = page.bridge.pageData("subtitle")
        page.cfg = d ? d : {}
        apply(page.cfg)
    }

    function apply(c) {
        var want = c.alignment || "bottom-center"
        for (var i = 0; i < alignCombo.model.length; i++) {
            if (alignCombo.model[i].key === want) { alignCombo.currentIndex = i; break }
        }
        posField.text = fmt(c.position_ratio, 0.06)
        sizeField.text = fmt(c.font_size_ratio, 0.045)
        faceField.text = c.font_face || "Microsoft YaHei"
        colorField.text = c.color || "#FFFFFF"
        backField.text = c.back_color || "#000000"
        opacityField.text = fmt(c.opacity, 1.0)
        outlineField.text = fmt(c.outline, 2.0)
        boxRadio.checked = (c.border_style || "box") === "box"
        outlineRadio.checked = !boxRadio.checked
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
            text: "位置与字号"
            color: SettingsTheme.text
            font.pixelSize: SettingsTheme.bodySize
            font.weight: Font.DemiBold
        }

        SettingRow {
            Layout.fillWidth: true
            label: "对齐位置"
            SettingsComboBox {
                id: alignCombo
                objectName: "alignmentCombo"
                Layout.preferredWidth: 200
                textRole: "label"
                valueRole: "key"
                model: page.meta && page.meta.alignments ? page.meta.alignments : []
                // ComboBox 自带的 currentValue 就是 valueRole 取到的 key
                function alignment() { return currentValue !== undefined ? currentValue : "bottom-center" }
            }
        }

        SettingRow {
            Layout.fillWidth: true
            label: "距边距离比例"
            hint: "距对应边的距离 = 视频高度 × 该值（0 ~ 0.4，0.06 ≈ 6%）"
            TextField {
                id: posField
                objectName: "positionField"
                Layout.preferredWidth: 110
                validator: DoubleValidator { bottom: 0; top: 0.4; decimals: 3 }
            }
        }

        SettingRow {
            Layout.fillWidth: true
            label: "字号比例"
            hint: "0.01 ~ 0.12"
            TextField {
                id: sizeField
                objectName: "fontSizeField"
                Layout.preferredWidth: 110
                validator: DoubleValidator { bottom: 0.01; top: 0.12; decimals: 3 }
            }
        }

        SettingRow {
            Layout.fillWidth: true
            label: "字体"
            TextField {
                id: faceField
                objectName: "faceField"
                Layout.fillWidth: true
                Layout.maximumWidth: 280
                placeholderText: "Microsoft YaHei"
            }
        }
    }

    ESpringCard {
        Layout.fillWidth: true
        cardColor: SettingsTheme.card
        borderColor: SettingsTheme.stroke

        Text {
            Layout.fillWidth: true
            text: "颜色与边框"
            color: SettingsTheme.text
            font.pixelSize: SettingsTheme.bodySize
            font.weight: Font.DemiBold
        }

        SettingRow {
            Layout.fillWidth: true
            label: "文字颜色"
            hint: "#RRGGBB"
            TextField {
                id: colorField
                objectName: "colorField"
                Layout.preferredWidth: 130
                placeholderText: "#FFFFFF"
            }
            Rectangle {           // 色块预览
                Layout.preferredWidth: 26
                Layout.preferredHeight: 26
                radius: 6
                color: page.validColor(colorField.text) ? colorField.text : "transparent"
                border.color: SettingsTheme.stroke
                border.width: 1
            }
        }

        SettingRow {
            Layout.fillWidth: true
            label: "底框颜色"
            hint: "#RRGGBB"
            TextField {
                id: backField
                objectName: "backColorField"
                Layout.preferredWidth: 130
                placeholderText: "#000000"
            }
            Rectangle {
                Layout.preferredWidth: 26
                Layout.preferredHeight: 26
                radius: 6
                color: page.validColor(backField.text) ? backField.text : "transparent"
                border.color: SettingsTheme.stroke
                border.width: 1
            }
        }

        SettingRow {
            Layout.fillWidth: true
            label: "不透明度 (0-1)"
            hint: "0.05 ~ 1.0"
            TextField {
                id: opacityField
                objectName: "opacityField"
                Layout.preferredWidth: 110
                validator: DoubleValidator { bottom: 0.05; top: 1.0; decimals: 3 }
            }
        }

        SettingRow {
            Layout.fillWidth: true
            label: "描边宽度"
            hint: "0 ~ 6"
            TextField {
                id: outlineField
                objectName: "outlineField"
                Layout.preferredWidth: 110
                validator: DoubleValidator { bottom: 0; top: 6; decimals: 2 }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 1
            Layout.topMargin: 4
            Layout.bottomMargin: 4
            color: SettingsTheme.stroke
        }

        Text {
            Layout.fillWidth: true
            text: "边框样式"
            color: SettingsTheme.text
            font.pixelSize: SettingsTheme.bodySize
        }

        ButtonGroup { id: borderGroup }

        RadioButton {
            id: boxRadio
            objectName: "borderBox"
            text: "不透明底框（推荐，任何画面都清晰）"
            ButtonGroup.group: borderGroup
        }

        RadioButton {
            id: outlineRadio
            objectName: "borderOutline"
            text: "仅描边（不遮挡画面）"
            ButtonGroup.group: borderGroup
        }
    }

    // 保存入口（PageActions 与自检脚本都走这里）
    function save() {
        if (!page.bridge) return
        page.report(page.bridge.saveSubtitle({
            alignment: alignCombo.alignment(),
            position_ratio: posField.text,
            font_size_ratio: sizeField.text,
            font_face: faceField.text,
            color: colorField.text,
            back_color: backField.text,
            opacity: opacityField.text,
            outline: outlineField.text,
            border_style: boxRadio.checked ? "box" : "outline"
        }))
    }

    PageActions {
        Layout.fillWidth: true
        primaryText: "保存"
        secondaryText: "恢复默认"
        status: page.statusText
        statusOk: page.statusOk

        onSecondaryClicked: {
            if (!page.bridge) return
            page.apply(page.bridge.subtitleDefaults())
            page.statusOk = true
            page.statusText = "已填入默认值，点「保存」后写入。"
        }

        onPrimaryClicked: page.save()
    }

    // 色块预览用：只接受 #RRGGBB / #AARRGGBB
    function validColor(s) {
        return /^#([0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/.test(String(s || "").trim())
    }
}
