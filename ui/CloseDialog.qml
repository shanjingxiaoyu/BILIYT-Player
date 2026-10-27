import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import ".."
import "euikit"

// ============================================================
// 关闭询问对话框 —— 点主窗口 X 之后问「关闭程序 / 最小化到托盘」
//
// 根是 Window（不是 Dialog）：离屏截图脚本 _shot.py 用
// QQmlApplicationEngine 加载，必须能直接从 rootObjects() 拿到窗口。
// 默认 visible:false，由调用方 open()（Lead 侧先 load 再按需打开）。
//
// 接口（见 ui-kit/CONTRACT.md）：
//   property bool   dark      深浅色，默认绑 Theme.dark
//   property string action    "close" | "tray"，默认 tray（不会误关监听）
//   property bool   remember  "以后不再显示"，默认勾选
//   signal accepted(string action, bool remember)
//   signal cancelled()
//   function open()           显示并居中（优先居中于 dialogParent）
//
// 注意：palette 必须显式给。离屏 / 无主题环境下
// Application.styleHints.colorScheme 是 Unknown，Fluent 样式会按浅色
// 底色 + 深色字之外的情况选白色文字，导致控件文字与 Theme.card 撞色看不见。
// ============================================================
Window {
    id: root

    property bool dark: Theme.dark
    property string action: "tray"
    property bool remember: true

    // 调用方可在 open() 前塞入父窗口，用于居中。
    // 类型必须是 var 而不是 Item：Window 不是 Item（它是 QQuickWindow），
    // 声明成 Item 会让 Lead 侧 mainWindow 赋不进来。
    property var dialogParent: null

    signal accepted(string action, bool remember)
    signal cancelled()

    title: "关闭 BiliYTPlayer"
    visible: false
    flags: Qt.Dialog | Qt.WindowTitleHint | Qt.WindowCloseButtonHint
    modality: Qt.WindowModal
    color: Theme.card

    width: 420
    height: 316
    // 固定尺寸，禁止缩放（Fluent 对话框宽度是定值）
    minimumWidth: width
    maximumWidth: width
    minimumHeight: height
    maximumHeight: height

    // 强制把控件文字色对齐 Theme，见文件头注释
    palette.window: Theme.card
    palette.windowText: Theme.text
    palette.base: Theme.cardAlt
    palette.text: Theme.text
    palette.button: Theme.cardAlt
    palette.buttonText: Theme.text
    palette.highlight: Theme.accent
    palette.highlightedText: root.dark ? "#1B1B1B" : "#FFFFFF"
    palette.placeholderText: Theme.text3

    // ---- 打开：置中 + 置顶 + 激活 ----
    // dialogParent 可能是 Window（Lead 的主窗口）也可能是 Item：
    // Item 用 mapToGlobal 取屏幕坐标，Window 没有这个方法，但它的 x/y 本身就是屏幕坐标。
    // WindowModal 必须配 transientParent，否则对话框可能跑到主窗口后面。
    function open() {
        var gx = 0, gy = 0, gw = 0, gh = 0
        if (dialogParent) {
            if (typeof dialogParent.mapToGlobal === "function") {
                // Item：屏幕坐标要换算，父窗口从 attached property 取
                var p = dialogParent.mapToGlobal(0, 0)
                gx = p.x; gy = p.y
                transientParent = dialogParent.Window.window
            } else {
                // Window：x/y 已是屏幕坐标
                gx = dialogParent.x; gy = dialogParent.y
                transientParent = dialogParent
            }
            gw = dialogParent.width
            gh = dialogParent.height
        }
        if (gw > 0 && gh > 0) {
            x = Math.round(gx + (gw - width) / 2)
            y = Math.round(gy + (gh - height) / 2)
        } else {
            x = Math.round((Screen.width - width) / 2)
            y = Math.round((Screen.height - height) / 2)
        }
        visible = true
        raise()
        requestActivate()
    }

    // ---- 结果 ----
    function _accept() {
        visible = false
        root.accepted(root.action, root.remember)
    }

    function _cancel() {
        visible = false
        root.cancelled()
    }

    // 外部改了 action，两个单选的勾选状态要同步回来。
    // guard 是必要的：createWithInitialProperties 之类的路径会在子项创建前就设 action。
    onActionChanged: {
        if (rbClose) rbClose.checked = (root.action === "close")
        if (rbTray) rbTray.checked = (root.action === "tray")
    }

    // 键盘：Enter = 确定，Esc = 取消
    Shortcut { sequence: "Return"; onActivated: root._accept() }
    Shortcut { sequence: "Enter"; onActivated: root._accept() }
    Shortcut { sequence: "Escape"; onActivated: root._cancel() }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 6

        Text {
            text: "关闭 BiliYTPlayer"
            color: Theme.text
            font.pixelSize: 17
            font.bold: true
        }

        Text {
            text: "你想关闭程序，还是让它在后台继续监听剪贴板？"
            color: Theme.text2
            font.pixelSize: 13
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        Item { Layout.preferredHeight: 6 }

        // ---- 单选一：关闭程序 ----
        RadioButton {
            id: rbClose
            text: "关闭程序"
            checked: root.action === "close"
            onClicked: root.action = "close"
        }
        Text {
            text: "停止剪贴板监听并退出"
            color: Theme.text3
            font.pixelSize: 12
            Layout.leftMargin: 28
        }

        // ---- 单选二：最小化到托盘 ----
        RadioButton {
            id: rbTray
            text: "最小化到托盘"
            checked: root.action === "tray"
            onClicked: root.action = "tray"
        }
        Text {
            text: "继续在后台监听剪贴板，点托盘图标恢复"
            color: Theme.text3
            font.pixelSize: 12
            Layout.leftMargin: 28
        }

        Item { Layout.fillHeight: true }

        CheckBox {
            id: cbRemember
            text: "以后不再显示这个询问"
            checked: root.remember
            onToggled: root.remember = checked
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Item { Layout.fillWidth: true }

            ESpringButton {
                text: "取消"
                onClicked: root._cancel()
            }
            ESpringButton {
                text: "确定"
                highlighted: true
                onClicked: root._accept()
            }
        }
    }
}
