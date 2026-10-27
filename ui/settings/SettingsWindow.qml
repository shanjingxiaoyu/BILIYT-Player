import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import "../euikit"

// ============================================================
// 设置窗口 —— Fluent 设置页布局：左侧 NavigationView 式页签，右侧内容区
//
// 根元素必须是 Window（不是 Dialog / ApplicationWindow），由 Python 侧
// engine.load() 后调 open() 显示，见 ui-kit/CONTRACT.md。
//
// 深浅色跟随系统：SettingsTheme.* 给自定义容器上色，Fluent 控件自身由样式驱动。
// 未定义 settingsBridge 时（离屏预览/截图）退化为只读桩，不崩。
// ============================================================
Window {
    id: win

    // ---- 契约要求的接口 ----
    visible: false
    function open() {
        win.visible = true
        win.raise()
        win.requestActivate()
        reload()
    }

    // ---- 供 _shot_wrapper.qml 注入桩数据（正常运行时保持默认 null）----
    property var mockBridge: null
    property string initialPage: "general"

    readonly property var bridge: mockBridge !== null
                                 ? mockBridge
                                 : (typeof settingsBridge !== "undefined" ? settingsBridge : null)

    // 下拉选项（对齐位置 / 跳过档位 / 画质 / 自启方式），一次读好各页共用
    property var meta: ({})
    property string currentKey: "general"

    width: 980
    height: 720
    minimumWidth: 800
    minimumHeight: 540
    title: "设置"
    color: SettingsTheme.windowBg

    // 把 Theme 的文字/底色灌进 palette：Fluent 控件的 contentItem 读
    // control.palette.text，离屏（offscreen QPA 报 colorScheme=Unknown）时
    // 系统 palette 会退化成深色，不显式喂就会白底白字。
    palette.window: SettingsTheme.windowBg
    palette.windowText: SettingsTheme.text
    palette.text: SettingsTheme.text
    palette.buttonText: SettingsTheme.text
    palette.base: SettingsTheme.card
    palette.button: SettingsTheme.cardAlt
    palette.highlight: SettingsTheme.accent
    palette.highlightedText: SettingsTheme.dark ? "#1B1B1B" : "#FFFFFF"

    readonly property var pageKeys: ["general", "playback", "danmaku",
                                     "subtitle", "sponsor", "account"]

    // 每次打开都重读 conf：外部（主窗口开关、手改文件）可能已经变了
    function reload() {
        if (!win.bridge) return
        try {
            win.meta = win.bridge.pageData("meta") || ({})
        } catch (e) {
            win.meta = ({})
        }
    }

    Component.onCompleted: {
        win.currentKey = win.initialPage
        reload()
    }

    // Lead 在 open() 之前 setProperty("initialPage", page) 指定要显示的页签。
    // 只在属性变化时跟随，用户手动切过的页签不会在重开时被弹回去。
    onInitialPageChanged: {
        if (win.initialPage)
            win.currentKey = win.initialPage
    }

    // 关窗就是隐藏：窗口实例由 Lead 持有，再次 open() 要能立刻弹出。
    // 不要写 onClosing 拦 close() —— Lead 已经 setQuitOnLastWindowClosed(False)，
    // 默认行为本来就只是隐藏；拦截反而会让 app.quit() 卡住（实测截图脚本
    // 在 grab 后调 app.quit() 永久不返回）。

    RowLayout {
        anchors.fill: parent
        spacing: 0

        // ------------------------------------------------ 左侧导航
        Rectangle {
            Layout.preferredWidth: 208
            Layout.fillHeight: true
            color: SettingsTheme.cardAlt

            Rectangle {                     // 右侧分隔线
                anchors.right: parent.right
                width: 1
                height: parent.height
                color: SettingsTheme.stroke
            }

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 12
                spacing: 4

                Text {
                    Layout.fillWidth: true
                    Layout.leftMargin: 6
                    Layout.topMargin: 2
                    Layout.bottomMargin: 6
                    text: "设置"
                    color: SettingsTheme.text
                    font.pixelSize: 18
                    font.weight: Font.DemiBold
                }

                NavItem {
                    Layout.fillWidth: true
                    pageKey: "general"; label: "通用"
                    current: win.currentKey === "general"
                    onActivated: win.currentKey = "general"
                }
                NavItem {
                    Layout.fillWidth: true
                    pageKey: "playback"; label: "画质"
                    current: win.currentKey === "playback"
                    onActivated: win.currentKey = "playback"
                }
                NavItem {
                    Layout.fillWidth: true
                    pageKey: "danmaku"; label: "弹幕"
                    current: win.currentKey === "danmaku"
                    onActivated: win.currentKey = "danmaku"
                }
                NavItem {
                    Layout.fillWidth: true
                    pageKey: "subtitle"; label: "字幕"
                    current: win.currentKey === "subtitle"
                    onActivated: win.currentKey = "subtitle"
                }
                NavItem {
                    Layout.fillWidth: true
                    pageKey: "sponsor"; label: "广告跳过"
                    current: win.currentKey === "sponsor"
                    onActivated: win.currentKey = "sponsor"
                }
                NavItem {
                    Layout.fillWidth: true
                    pageKey: "account"; label: "B站账号"
                    current: win.currentKey === "account"
                    onActivated: win.currentKey = "account"
                }

                Item { Layout.fillHeight: true }

                Text {
                    Layout.fillWidth: true
                    Layout.leftMargin: 6
                    text: "保存后即刻生效（部分播放设置将在下一个视频中应用）。"
                    color: SettingsTheme.text3
                    font.pixelSize: 10
                    wrapMode: Text.WordWrap
                }
            }
        }

        // ------------------------------------------------ 右侧内容
        StackLayout {
            id: pages
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: Math.max(0, win.pageKeys.indexOf(win.currentKey))

            GeneralPage {
                bridge: win.bridge; meta: win.meta
                onSaved: function(msg) { toast.show(msg) }
            }
            PlaybackPage {
                bridge: win.bridge; meta: win.meta
                onSaved: function(msg) { toast.show(msg) }
            }
            DanmakuPage {
                bridge: win.bridge; meta: win.meta
                onSaved: function(msg) { toast.show(msg) }
            }
            SubtitlePage {
                bridge: win.bridge; meta: win.meta
                onSaved: function(msg) { toast.show(msg) }
            }
            SponsorPage {
                bridge: win.bridge; meta: win.meta
                onSaved: function(msg) { toast.show(msg) }
            }
            AccountPage {
                bridge: win.bridge; meta: win.meta
                onSaved: function(msg) { toast.show(msg) }
            }
        }
    }

    // 保存成功的统一反馈（页内还有一行状态文字，两者互补）
    ESpringToast {
        id: toast
        anchors.fill: parent
        bgColor: SettingsTheme.dark ? "#3A3A3A" : "#2B2B2B"
        textColor: "#FFFFFF"
    }

    // 自检脚本用的入口：按页签名取页面对象，直接调它的 save()。
    // 这样 _verify_settings.py 能验证「QML 收集控件值 → bridge 落盘」整条链路，
    // 而不是只验 bridge 一层。
    function page(name) {
        var idx = win.pageKeys.indexOf(name)
        return idx >= 0 ? pages.children[idx] : null
    }
}
