import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Effects
import "euikit"
import "."

// ============================================================
// 主窗口 —— 对应原 tkinter 版 _build_ui（第 258-336 行，79 行）
//
//   ttk.LabelFrame("状态") + tk.Text      → ESpringCard + ListView（只读追加）
//   ttk.LabelFrame("播放历史") + Treeview  → ESpringCard + ListView + ESpringListDelegate
//   6 个 ttk.Button                       → 4 个 ESpringButton（3 个设置按钮合并进独立设置页）
//   3 个 ttk.Checkbutton                  → 3 个 ESpringSwitch
//
// 播放画面/进度条/弹幕全在 mpv，这里只管控制面板。
// ============================================================
ApplicationWindow {
    id: win
    width: 720
    height: 660
    minimumWidth: 600
    minimumHeight: 520
    visible: true
    title: "剪贴板直连播放器 — B站 / YouTube"
    color: Theme.windowBg

    // Fluent 样式的控件文字色取自 palette，而 palette 默认跟随系统的
    // colorScheme；在 colorScheme 未知（无桌面 / 高对比 / 某些远程会话）时
    // 会选白色文字，跟浅色卡片撞成一片白看不见。这里显式对齐 Theme。
    palette.window: Theme.windowBg
    palette.windowText: Theme.text
    palette.base: Theme.cardAlt
    palette.text: Theme.text
    palette.button: Theme.cardAlt
    palette.buttonText: Theme.text
    palette.highlight: Theme.accent
    palette.highlightedText: dark ? "#1B1B1B" : "#FFFFFF"
    palette.placeholderText: Theme.text3

    property bool dark: Theme.dark
    readonly property bool reduceMotion: false

    // ================= 顶部标题 =================
    header: ToolBar {
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 16
            anchors.rightMargin: 12
            spacing: 10

            Rectangle {
                width: 24; height: 24; radius: 6
                color: Theme.accent
                Text {
                    anchors.centerIn: parent
                    text: "▶"
                    color: "#FFFFFF"
                    font.pixelSize: 12
                    font.bold: true
                }
            }
            Text {
                text: "剪贴板直连播放器"
                font.pixelSize: 14
                font.bold: true
                color: Theme.text
            }
            Text {
                text: "B站 / YouTube"
                font.pixelSize: 12
                color: Theme.text2
            }
            Item { Layout.fillWidth: true }
            // 监听状态徽标
            Rectangle {
                visible: bridge.ready
                width: stateRow.implicitWidth + 16
                height: 22
                radius: 11
                color: bridge.paused
                       ? (win.dark ? Qt.rgba(1, 0.72, 0.2, 0.18) : Qt.rgba(0.84, 0.54, 0, 0.14))
                       : (win.dark ? Qt.rgba(0.42, 0.80, 0.37, 0.18) : Qt.rgba(0.06, 0.55, 0.16, 0.14))
                Row {
                    id: stateRow
                    anchors.centerIn: parent
                    spacing: 5
                    Rectangle {
                        width: 6; height: 6; radius: 3
                        anchors.verticalCenter: parent.verticalCenter
                        color: bridge.paused ? (win.dark ? "#FFC83D" : "#D68A00")
                                             : (win.dark ? "#6CCB5F" : "#107C10")
                    }
                    Text {
                        text: bridge.paused ? "已暂停" : "监听中"
                        font.pixelSize: 11
                        color: bridge.paused ? (win.dark ? "#FFC83D" : "#A05F00")
                                             : (win.dark ? "#6CCB5F" : "#0B6A0B")
                    }
                }
            }
        }
    }

    // ================= 主体 =================
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 12

        // ── 日志卡片 ────────────────────────────────────────────
        ESpringCard {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.preferredHeight: 280
            padding: 12
            cardColor: Theme.card
            borderColor: Theme.stroke
            entrance: true

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Text {
                    text: "状态"
                    font.pixelSize: Theme.titleSize
                    font.bold: true
                    color: Theme.text
                }
                Item { Layout.fillWidth: true }
                Text {
                    text: logModel.count + " 行"
                    font.pixelSize: 11
                    color: Theme.text3
                }
            }

            // 只读追加日志：必须是 ListView + ListModel（不是 TextArea，
            // TextArea 是输入框，带 placeholderText，语义与行为都不对）
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                radius: Theme.radius
                color: Theme.cardAlt
                border.color: Theme.stroke
                border.width: 1
                clip: true

                ListView {
                    id: logView
                    anchors.fill: parent
                    anchors.margins: 8
                    clip: true
                    model: logModel
                    spacing: 1
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                    delegate: Text {
                        width: logView.width
                        text: model.line
                        color: model.kind === "err" ? Theme.danger
                             : model.kind === "ok" ? Theme.ok
                             : Theme.text2
                        font.pixelSize: 12
                        font.family: "Consolas, Microsoft YaHei"
                        wrapMode: Text.WrapAnywhere
                    }

                    onCountChanged: if (logView.count > 0) logView.positionViewAtEnd()
                }
            }
        }

        // ── 播放历史卡片 ────────────────────────────────────────
        ESpringCard {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.preferredHeight: 220
            padding: 12
            cardColor: Theme.card
            borderColor: Theme.stroke
            entrance: true

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Text {
                    text: "播放历史"
                    font.pixelSize: Theme.titleSize
                    font.bold: true
                    color: Theme.text
                }
                Text {
                    text: "双击条目重播"
                    font.pixelSize: 11
                    color: Theme.text3
                }
                Item { Layout.fillWidth: true }
                Text {
                    visible: histModel.count === 0
                    text: "复制 B 站 / YouTube 链接即可开始"
                    font.pixelSize: 11
                    color: Theme.text3
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                radius: Theme.radius
                color: Theme.cardAlt
                border.color: Theme.stroke
                border.width: 1
                clip: true

                ListView {
                    id: histView
                    anchors.fill: parent
                    anchors.margins: 4
                    clip: true
                    model: histModel
                    spacing: 1
                    currentIndex: -1
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                    // hover 统一由 ListView 判定：indexAt() 只可能命中唯一一行，
                    // 避免 delegate 复用时多行 containsMouse 残留 → 看起来像多选。
                    property int hoveredIndex: -1

                    MouseArea {
                        anchors.fill: parent
                        hoverEnabled: true
                        acceptedButtons: Qt.NoButton   // 只收 hover，不吃点击
                        onPositionChanged: function(mouse) {
                            histView.hoveredIndex = histView.indexAt(mouse.x, mouse.y)
                        }
                        onExited: histView.hoveredIndex = -1
                    }

                    delegate: ESpringListDelegate {
                        required property int index
                        required property string time
                        required property string platform
                        required property string title
                        required property string detail

                        width: histView.width
                        rowHeight: 34
                        accentColor: Theme.accent
                        isDark: Theme.dark
                        textColor: Theme.text
                        selected: ListView.isCurrentItem

                        hoverManaged: true
                        hoveredExternally: histView.hoveredIndex === index

                        onClicked: histView.currentIndex = index
                        onDoubleClicked: {
                            histView.currentIndex = index
                            bridge.replay(index)
                        }

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 12
                            anchors.rightMargin: 10
                            spacing: 10

                            Text {
                                text: time
                                font.pixelSize: 11
                                color: Theme.text3
                                Layout.preferredWidth: 40
                            }
                            Text {
                                text: platform
                                font.pixelSize: 11
                                font.bold: true
                                color: Theme.accent
                                Layout.preferredWidth: 62
                                elide: Text.ElideRight
                            }
                            Text {
                                text: title
                                font.pixelSize: 12
                                color: Theme.text
                                Layout.fillWidth: true
                                elide: Text.ElideRight
                            }
                            Text {
                                text: detail
                                font.pixelSize: 11
                                color: Theme.text3
                                Layout.maximumWidth: 230
                                elide: Text.ElideRight
                                horizontalAlignment: Text.AlignRight
                            }
                        }
                    }
                }

                Text {
                    anchors.centerIn: parent
                    visible: histModel.count === 0
                    text: "暂无播放记录"
                    font.pixelSize: 12
                    color: Theme.text3
                }
            }
        }

        // ── 按钮栏 ──────────────────────────────────────────────
        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            ESpringButton {
                text: bridge.paused ? "恢复监听" : "暂停监听"
                enabled: bridge.ready
                motionEnabled: !win.reduceMotion
                onClicked: bridge.togglePause()
            }
            ESpringButton {
                text: "清空历史"
                enabled: histModel.count > 0
                motionEnabled: !win.reduceMotion
                onClicked: bridge.clearHistory()
            }

            Item { Layout.fillWidth: true }

            ESpringButton {
                text: "设置…"
                motionEnabled: !win.reduceMotion
                onClicked: bridge.openSettings("")
            }
            ESpringButton {
                text: "退出"
                motionEnabled: !win.reduceMotion
                onClicked: win.requestQuit()
            }
        }

        // ── 三个功能开关 ────────────────────────────────────────
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 52
            radius: Theme.radiusCard
            color: Theme.card
            border.color: Theme.stroke
            border.width: 1

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 16
                anchors.rightMargin: 16
                spacing: 22

                ESpringSwitch {
                    text: "自动跳过 UP 主广告"
                    checked: bridge.skipEnabled
                    motionEnabled: !win.reduceMotion
                    onToggled: bridge.skipEnabled = checked
                }
                ESpringSwitch {
                    text: "显示弹幕"
                    checked: bridge.danmakuEnabled
                    motionEnabled: !win.reduceMotion
                    onToggled: bridge.danmakuEnabled = checked
                }
                ESpringSwitch {
                    text: "显示原生字幕"
                    checked: bridge.subtitleEnabled
                    motionEnabled: !win.reduceMotion
                    onToggled: bridge.subtitleEnabled = checked
                }
                Item { Layout.fillWidth: true }
            }
        }
    }

    // ================= Toast =================
    ESpringToast {
        id: toast
        anchors.fill: parent
        bgColor: Theme.dark ? "#3A3A3A" : "#2B2B2B"
        textColor: "#FFFFFF"
    }

    // ================= 数据模型 =================
    ListModel { id: logModel }
    ListModel { id: histModel }

    // ================= bridge 信号接线 =================
    Connections {
        target: bridge

        function onLogLine(line) {
            var kind = "info"
            if (line.indexOf("[!]") >= 0) kind = "err"
            else if (line.indexOf("[+]") >= 0) kind = "ok"
            logModel.append({ line: line, kind: kind })
            // 日志上限：防止长时间运行把 ListModel 撑爆（原 tk.Text 同样无限增长，
            // 这里给个宽松上限，滚掉最早的 200 行）
            if (logModel.count > 2000)
                logModel.remove(0, 200)
        }

        function onHistoryAdded(time, platform, title, detail) {
            histModel.append({ time: time, platform: platform, title: title, detail: detail })
            histView.positionViewAtEnd()
        }

        function onHistoryCleared() { histModel.clear() }

        function onToast(msg) { toast.show(msg) }

        function onPausedChanged() {
            // 按钮文字由绑定自动跟随 bridge.paused，这里只做提示
        }

        function onFirstRunPrompt() {
            toast.show("请先在设置中填写 SESSDATA")
        }

        // ---- 托盘菜单 / 自启住托盘（由 Python 侧 emit）----
        function onTrayShowRequested() {
            win.show()
            win.raise()
            win.requestActivate()
        }
        function onTraySettingsRequested() { bridge.openSettings("") }
        function onTrayQuitRequested() { win.requestQuit() }
        function onMinimizeToTrayRequested() { win.minimizeToTray() }
    }

    // ================= 关闭行为（点 X 之后）=================
    // 用户需求：点 X 时问「关闭程序 / 最小化到托盘」，带「以后不再显示」（默认勾选）；
    // 设置页的「点击 × 之后」也能预先指定默认行为。
    //
    // 判定顺序：
    //   1. askOnClose == true  → 弹询问框（预选设置里指定的那个动作；未指定则预选"托盘"）
    //   2. askOnClose == false → 直接执行设置里的动作，不再问
    //   3. 设置里没配过（close_action 仍是 "ask"）→ 当作询问
    property var closeDialog: null
    property var tray: null
    property bool _forceClose: false

    function requestQuit() {
        win._forceClose = true
        if (tray) tray.hide()
        if (typeof bridge.shutdown === "function") bridge.shutdown()
        Qt.quit()
    }

    function minimizeToTray() {
        win.hide()
        if (tray) {
            tray.show()
            tray.notify("BiliYTPlayer", "仍在后台监听剪贴板，复制链接即可播放")
        } else {
            // 无托盘环境下"最小化到托盘"等于窗口消失且唤不回 —— 直接退出更诚实
            win.requestQuit()
        }
    }

    onClosing: function (close) {
        if (win._forceClose) {
            close.accepted = true
            return
        }
        close.accepted = false

        var action = bridge.closeAction()          // "ask" | "close" | "tray"
        var ask = (action === "ask") || bridge.askOnClose()

        if (!ask) {
            if (action === "tray") win.minimizeToTray()
            else win.requestQuit()
            return
        }

        if (closeDialog) {
            // 预选：设置里明确选过就跟着走，没选过默认"托盘"（不会误关监听）
            closeDialog.action = (action === "close") ? "close" : "tray"
            closeDialog.remember = true
            closeDialog.open()
        } else {
            win.minimizeToTray()
        }
    }

    // 供 CloseDialog 的 accepted(action, remember) 回调
    function applyCloseChoice(action, remember) {
        bridge.saveCloseChoice(action, remember)
        if (action === "tray") win.minimizeToTray()
        else win.requestQuit()
    }

    // 关闭询问框由入口注入（property var closeDialog）。
    // 必须在 QML 侧接信号 —— QML 的 `function` 不是 Python 可调用属性，
    // 从 Python 直接 .connect 到 QML 函数会静默失败。
    Connections {
        target: win.closeDialog
        function onAccepted(action, remember) {
            win.applyCloseChoice(action, remember)
        }
    }

    Component.onCompleted: bridge.bootstrap()
}
