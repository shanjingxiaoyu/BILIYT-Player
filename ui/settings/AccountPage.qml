import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../euikit"

// B站账号 —— .env 里的 SESSDATA
//
// 只回显脱敏信息（前 4 位 + 总长度）；保存格式与 legacy/bili_yt_player.pyw:877 一致：
//     "# B 站登录态 Cookie\nSESSDATA=<值>\n"
SettingsPage {
    id: page

    property string masked: "未配置"
    property bool configured: false
    property bool busy: false

    title: "B站账号"
    subtitle: "SESSDATA 是 B 站网页端的登录态 Cookie，用于取 1080P 以上画质与字幕。"

    function load() {
        if (!page.bridge) return
        var d = page.bridge.reloadAccount()
        if (!d) return
        page.configured = !!d.configured
        page.masked = d.masked || "未配置"
    }

    function finish(ok, msg) {
        page.busy = false
        page.statusOk = !!ok
        page.statusText = msg || ""
        if (ok) {
            page.load()
            page.saved(msg || "已保存")
            sessField.text = ""
        }
    }

    Component.onCompleted: {
        load()
        if (typeof page.bridge.sessionStatus !== "undefined")
            page.bridge.sessionStatus.connect(finish)
    }

    ESpringCard {
        Layout.fillWidth: true
        cardColor: SettingsTheme.card
        borderColor: SettingsTheme.stroke

        Text {
            Layout.fillWidth: true
            text: "当前登录态"
            color: SettingsTheme.text
            font.pixelSize: SettingsTheme.bodySize
            font.weight: Font.DemiBold
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 10

            Rectangle {          // 状态圆点
                Layout.preferredWidth: 10
                Layout.preferredHeight: 10
                radius: 5
                color: page.configured ? SettingsTheme.ok : SettingsTheme.text3
                Layout.alignment: Qt.AlignVCenter
            }

            Text {
                Layout.fillWidth: true
                text: page.configured ? "已配置" : "未配置（播放 1080P 以上画质会受限）"
                color: page.configured ? SettingsTheme.ok : SettingsTheme.text2
                font.pixelSize: SettingsTheme.bodySize
            }

            Text {
                objectName: "maskedLabel"
                text: page.masked
                color: SettingsTheme.text3
                font.pixelSize: SettingsTheme.bodySize
                font.family: "Consolas"
            }
        }
    }

    ESpringCard {
        Layout.fillWidth: true
        cardColor: SettingsTheme.card
        borderColor: SettingsTheme.stroke

        Text {
            Layout.fillWidth: true
            text: "更新 SESSDATA"
            color: SettingsTheme.text
            font.pixelSize: SettingsTheme.bodySize
            font.weight: Font.DemiBold
        }

        Text {
            Layout.fillWidth: true
            text: "浏览器登录 bilibili.com → F12 → Application → Cookies → www.bilibili.com → 复制 SESSDATA 的 Value"
            color: SettingsTheme.text3
            font.pixelSize: 11
            wrapMode: Text.WordWrap
        }

        TextField {
            id: sessField
            objectName: "sessField"
            Layout.fillWidth: true
            Layout.maximumWidth: 520
            placeholderText: "粘贴 SESSDATA 值"
            echoMode: TextInput.Password
            selectByMouse: true
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            ESpringButton {
                objectName: "saveSessButton"
                text: page.busy ? "验证中…" : "保存并验证"
                enabled: !page.busy
                onClicked: {
                    if (!page.bridge) return
                    page.busy = true
                    page.statusOk = true
                    page.statusText = "正在向 B 站校验…"
                    // 校验是网络请求，bridge 里放后台线程，结果走 sessionStatus
                    if (!page.bridge.verifySessdata(sessField.text)) {
                        page.busy = false
                        page.statusOk = false
                        page.statusText = "请输入 SESSDATA"
                    }
                }
            }

            Item { Layout.fillWidth: true }
        }
    }

    PageActions {
        Layout.fillWidth: true
        primaryText: page.busy ? "验证中…" : "刷新状态"
        secondaryText: ""
        status: page.statusText
        statusOk: page.statusOk
        onPrimaryClicked: page.load()
    }
}
