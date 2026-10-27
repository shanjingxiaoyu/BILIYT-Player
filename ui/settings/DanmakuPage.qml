import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../euikit"

// 弹幕 —— danmaku.conf
//
// 数值范围与 legacy/bili_yt_player.pyw:547-562 逐条对应；**真正的钳制在
// ui/settings_bridge.py 里做**（QML 的 DoubleValidator 只是软约束，粘贴可绕过）。
SettingsPage {
    id: page

    property string cacheMsg: ""

    title: "弹幕"
    subtitle: "保存后播放下一个视频即生效，无需重启。"

    function load() {
        if (!page.bridge) return
        var d = page.bridge.pageData("danmaku")
        page.cfg = d ? d : {}
        opacityField.text = fmt(page.cfg.opacity, 0.7)
        fontSizeField.text = fmt(page.cfg.font_size_ratio, 0.04)
        marqueeField.text = fmt(page.cfg.duration_marquee, 12.0)
        stillField.text = fmt(page.cfg.duration_still, 5.0)
        regionField.text = fmt(page.cfg.display_region, 0.30)
        outlineField.text = fmt(page.cfg.outline, 2.0)
        faceField.text = page.cfg.font_face || "Microsoft YaHei"
        keywordField.text = page.cfg.block_keywords || ""
        topBox.checked = !!page.cfg.block_top
        bottomBox.checked = !!page.cfg.block_bottom
        scrollBox.checked = !!page.cfg.block_scroll
    }

    // 数值回填：去掉多余的小数零，0.7 不显示成 0.699999999
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
            text: "外观与时长"
            color: SettingsTheme.text
            font.pixelSize: SettingsTheme.bodySize
            font.weight: Font.DemiBold
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
            label: "字号比例"
            hint: "字号 = 视频高度 × 该值（0.01 ~ 0.20）"
            TextField {
                id: fontSizeField
                objectName: "fontSizeField"
                Layout.preferredWidth: 110
                validator: DoubleValidator { bottom: 0.01; top: 0.20; decimals: 3 }
            }
        }

        SettingRow {
            Layout.fillWidth: true
            label: "滚动存活（秒）"
            hint: "2 ~ 30"
            TextField {
                id: marqueeField
                objectName: "marqueeField"
                Layout.preferredWidth: 110
                validator: DoubleValidator { bottom: 2; top: 30; decimals: 2 }
            }
        }

        SettingRow {
            Layout.fillWidth: true
            label: "固定存活（秒）"
            hint: "1 ~ 20"
            TextField {
                id: stillField
                objectName: "stillField"
                Layout.preferredWidth: 110
                validator: DoubleValidator { bottom: 1; top: 20; decimals: 2 }
            }
        }

        SettingRow {
            Layout.fillWidth: true
            label: "显示区域 (0-1)"
            hint: "占画面上方比例，低于 0.3 轨道太少会丢大量弹幕"
            TextField {
                id: regionField
                objectName: "regionField"
                Layout.preferredWidth: 110
                validator: DoubleValidator { bottom: 0.3; top: 1.0; decimals: 3 }
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

        SettingRow {
            Layout.fillWidth: true
            label: "屏蔽关键词"
            hint: "逗号分隔，命中即丢弃该条弹幕"
            TextField {
                id: keywordField
                objectName: "keywordField"
                Layout.fillWidth: true
                Layout.maximumWidth: 280
                placeholderText: "例：前排，打卡"
            }
        }
    }

    ESpringCard {
        Layout.fillWidth: true
        cardColor: SettingsTheme.card
        borderColor: SettingsTheme.stroke

        Text {
            Layout.fillWidth: true
            text: "屏蔽类型"
            color: SettingsTheme.text
            font.pixelSize: SettingsTheme.bodySize
            font.weight: Font.DemiBold
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 20

            CheckBox {
                id: topBox
                objectName: "blockTop"
                text: "顶部固定弹幕"
            }
            CheckBox {
                id: bottomBox
                objectName: "blockBottom"
                text: "底部固定弹幕"
            }
            CheckBox {
                id: scrollBox
                objectName: "blockScroll"
                text: "滚动弹幕"
            }
            Item { Layout.fillWidth: true }
        }
    }

    // 保存入口（PageActions 与自检脚本都走这里）
    function save() {
        if (!page.bridge) return
        page.report(page.bridge.saveDanmaku({
            opacity: opacityField.text,
            font_size_ratio: fontSizeField.text,
            duration_marquee: marqueeField.text,
            duration_still: stillField.text,
            display_region: regionField.text,
            outline: outlineField.text,
            font_face: faceField.text,
            block_keywords: keywordField.text,
            block_top: topBox.checked,
            block_bottom: bottomBox.checked,
            block_scroll: scrollBox.checked
        }))
    }

    PageActions {
        Layout.fillWidth: true
        primaryText: "保存"
        showClearCache: true
        status: page.statusText
        statusOk: page.statusOk

        onClearCacheClicked: {
            if (!page.bridge) return
            var r = page.bridge.clearDanmakuCache()
            page.statusOk = !!(r && r.ok)
            page.statusText = (r && r.msg) ? r.msg : ""
        }

        onPrimaryClicked: page.save()
    }
}
