import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../euikit"

// 通用 —— 外观主题 / 关闭行为 / 启动设置
// 映射 general.conf：theme / close_action / ask_on_close / autostart_mode
//
// 文案按用户反馈逐条改过：去掉「点窗口 ×」「监听剪贴板」「注册表路径」这类
// 开发者语言与底层实现细节，换成普通用户能直接看懂的说法。
SettingsPage {
    id: page

    title: "通用"
    subtitle: "关闭主窗口时的行为及启动设置。（修改后立即生效）"

    function load() {
        if (!page.bridge) return
        var d = page.bridge.pageData("general")
        page.cfg = d ? d : {}
        themeGroup.sync()
        closeGroup.sync()
    }

    Component.onCompleted: load()

    // ------------------------------------------------------------ 外观主题
    // 放在最上面：这是整页唯一「保存后整个窗口立刻变样」的项，先换色更自然
    ESpringCard {
        Layout.fillWidth: true
        cardColor: SettingsTheme.card
        borderColor: SettingsTheme.stroke

        Text {
            Layout.fillWidth: true
            text: "外观主题"
            color: SettingsTheme.text
            font.pixelSize: SettingsTheme.bodySize
            font.weight: Font.DemiBold
        }

        ButtonGroup {
            id: themeGroup

            function chosen() {
                var items = [themeSystem, themeLight, themeDark]
                var keys = ["system", "light", "dark"]
                for (var i = 0; i < items.length; i++)
                    if (items[i].checked) return keys[i]
                return "system"
            }

            function sync() {
                var want = page.cfg.theme || "system"
                themeSystem.checked = want === "system"
                themeLight.checked = want === "light"
                themeDark.checked = want === "dark"
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 20

            RadioButton {
                id: themeSystem
                objectName: "themeSystem"
                text: "跟随系统"
                ButtonGroup.group: themeGroup
            }
            RadioButton {
                id: themeLight
                objectName: "themeLight"
                text: "浅色模式"
                ButtonGroup.group: themeGroup
            }
            RadioButton {
                id: themeDark
                objectName: "themeDark"
                text: "深色模式"
                ButtonGroup.group: themeGroup
            }
            Item { Layout.fillWidth: true }
        }

        Text {
            Layout.fillWidth: true
            text: "「跟随系统」会跟着 Windows 的应用模式自动切换。"
            color: SettingsTheme.text3
            font.pixelSize: 11
            wrapMode: Text.WordWrap
        }
    }

    // ------------------------------------------------------------ 关闭行为
    ESpringCard {
        Layout.fillWidth: true
        cardColor: SettingsTheme.card
        borderColor: SettingsTheme.stroke

        Text {
            Layout.fillWidth: true
            text: "关闭主窗口时"
            color: SettingsTheme.text
            font.pixelSize: SettingsTheme.bodySize
            font.weight: Font.DemiBold
        }

        ButtonGroup {
            id: closeGroup

            function chosen() {
                if (closeRadio.checked) return "close"
                if (trayRadio.checked) return "tray"
                return "close"
            }

            // 把 conf 值同步到单选状态（"ask" 老默认值显示成"关闭程序"）
            function sync() {
                trayRadio.checked = page.cfg.close_action === "tray"
                closeRadio.checked = !trayRadio.checked
            }
        }

        RadioButton {
            id: closeRadio
            objectName: "closeActionClose"
            text: "关闭程序"
            checked: page.cfg.close_action !== "tray"
            ButtonGroup.group: closeGroup
        }

        RadioButton {
            id: trayRadio
            objectName: "closeActionTray"
            text: "最小化到系统托盘（在后台自动识别剪贴板链接）"
            ButtonGroup.group: closeGroup
        }

        Text {
            Layout.fillWidth: true
            Layout.topMargin: 2
            visible: page.cfg.close_action === "ask"
            text: "当前配置是每次都询问；选定一项并保存后才会固定行为。"
            color: SettingsTheme.text3
            font.pixelSize: 11
            wrapMode: Text.WordWrap
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 1
            Layout.topMargin: 4
            Layout.bottomMargin: 4
            color: SettingsTheme.stroke
        }

        CheckBox {
            id: askBox
            objectName: "askOnClose"
            text: "关闭前弹窗确认"
            checked: page.cfg.ask_on_close !== false
        }

        Text {
            Layout.fillWidth: true
            text: "取消勾选后，下次关闭将直接执行上述选择，不再弹窗提示。"
            color: SettingsTheme.text3
            font.pixelSize: 11
            wrapMode: Text.WordWrap
        }
    }

    // ------------------------------------------------------------ 启动设置
    ESpringCard {
        Layout.fillWidth: true
        cardColor: SettingsTheme.card
        borderColor: SettingsTheme.stroke

        Text {
            Layout.fillWidth: true
            text: "启动设置"
            color: SettingsTheme.text
            font.pixelSize: SettingsTheme.bodySize
            font.weight: Font.DemiBold
        }

        SettingRow {
            Layout.fillWidth: true
            label: "自动启动"

            SettingsComboBox {
                id: autoCombo
                objectName: "autostartCombo"
                Layout.preferredWidth: 220
                textRole: "label"
                valueRole: "key"
                model: page.meta && page.meta.autostart ? page.meta.autostart : []
                currentIndex: {
                    var want = page.cfg.autostart_mode || "none"
                    for (var i = 0; i < model.length; i++)
                        if (model[i].key === want) return i
                    return 0
                }
                // ComboBox 自带的 currentValue 就是 valueRole 取到的 key
                function mode() { return currentValue !== undefined ? currentValue : "none" }
            }
        }

        // 状态就一行小字：不再显示注册表路径（用户看不懂，还容易联想到安全软件）
        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Rectangle {
                Layout.preferredWidth: 8
                Layout.preferredHeight: 8
                radius: 4
                Layout.alignment: Qt.AlignVCenter
                color: page.cfg.autostart_enabled ? SettingsTheme.ok : SettingsTheme.text3
            }

            Text {
                objectName: "autostartStatus"
                text: page.autostartText()
                color: page.cfg.autostart_enabled ? SettingsTheme.ok : SettingsTheme.text3
                font.pixelSize: 11
            }
        }
    }

    // 自启状态的人话描述（不暴露注册表 / exe 路径等实现细节）
    function autostartText() {
        if (page.cfg.autostart_enabled) return "已开启"
        // 源码运行时取不到 exe 路径，这不是错误 —— 打包后就能用
        if (page.cfg.autostart_available === false) return "打包为 exe 后可开启"
        return "已关闭"
    }

    // 保存入口（PageActions 与自检脚本都走这里）
    function save() {
        if (!page.bridge) return
        page.report(page.bridge.saveGeneral({
            close_action: closeGroup.chosen(),
            ask_on_close: askBox.checked,
            autostart_mode: autoCombo.mode(),
            theme: themeGroup.chosen()
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
