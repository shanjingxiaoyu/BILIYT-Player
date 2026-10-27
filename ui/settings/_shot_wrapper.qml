import QtQuick
import QtQuick.Controls

// ============================================================
// 离屏截图用的包装层（开发期，不进发行包）
//
// **优先使用真实 settingsBridge**：_verify_settings.py 用 `--render` 模式跑
// 子进程时会注入真实的 SettingsBridge，这条通路才和 exe 里一致。
// 只有裸跑 `_shot.py`（不注入任何上下文属性）时才退回下面的桩。
//
// 教训：之前这里只有桩，桩是 QML 原生 JS 对象，永远「数据是好的」，
// 所以 ComboBox 全空那种真实通路 bug 一张截图也测不出来。
//
// 页签取自 Qt.application.arguments 的第 7 个参数（_shot.py 传的宽/高/方案
// 之后还会多出来一个），缺省显示「通用」：
//
//   python _shot.py ui/settings/_shot_wrapper.qml out.png 980 720 light
//   python _shot.py ui/settings/_shot_wrapper.qml out.png 980 720 dark danmaku
//
// 桩数据的形状与 ui/settings_bridge.py 的 pageData() 返回值一致。
// ============================================================
SettingsWindow {
    id: wrapper

    // 有真实 bridge 就用真的（mockBridge = null 时 SettingsWindow 自己回落）
    mockBridge: (typeof settingsBridge !== "undefined" && settingsBridge !== null)
                ? null : stub
    initialPage: {
        // argv: [0]=_shot.py [1]=qml [2]=png [3]=w [4]=h [5]=scheme [6]=页签
        var a = Qt.application.arguments
        return a.length > 6 ? String(a[6]) : "general"
    }
    visible: true

    // 截图时把 Fluent 样式的深浅色也一起对齐。
    // Theme 的 __forceScheme 只影响我们自己的 token；Fluent 控件读的是
    // Application.styleHints.colorScheme（Windows 平台可写，offscreen 平台忽略）。
    Component.onCompleted: {
        var force = (typeof __forceScheme !== "undefined") ? String(__forceScheme) : ""
        if (force === "light")
            Application.styleHints.colorScheme = Qt.Light
        else if (force === "dark")
            Application.styleHints.colorScheme = Qt.Dark
    }

    // ---- 与 SettingsBridge 同形的桩（只实现页面渲染会用到的读取方法）----
    QtObject {
        id: stub

        function pageData(name) {
            if (name === "meta") return wrapper.meta
            if (name === "general") return wrapper.generalCfg
            if (name === "danmaku") return wrapper.danmakuCfg
            if (name === "subtitle") return wrapper.subtitleCfg
            if (name === "sponsor") return wrapper.sponsorCfg
            return ({})
        }
        function reloadAccount() { return wrapper.accountCfg }
        function saveGeneral() { return { ok: true, msg: "已保存（桩）" } }
        function saveDanmaku() { return { ok: true, msg: "已保存，播放下一个视频即生效（桩）" } }
        function saveSubtitle() { return { ok: true, msg: "已保存（桩）" } }
        function saveSponsor() { return { ok: true, msg: "已保存：自动跳过 6 类，手动跳过 4 类（桩）" } }
        function clearDanmakuCache() { return { ok: true, msg: "已清空弹幕缓存：12 个文件（桩）" } }
        function subtitleDefaults() { return wrapper.subtitleCfg }
        function verifySessdata() { return true }
    }

    // ---- 桩数据（真实界面里的取值示例）----
    // theme 按截图方案给：light 图里「浅色模式」应勾上，dark 图里「深色模式」应勾上，
    // 这样截图本身就能证明主题单选是通的。
    readonly property var generalCfg: ({
        close_action: "tray",
        ask_on_close: true,
        autostart_mode: "registry",
        quality: "126",
        autostart_enabled: true,
        autostart_available: true,
        theme: (typeof __forceScheme !== "undefined" && __forceScheme === "dark")
               ? "dark" : "light"
    })

    readonly property var danmakuCfg: ({
        enabled: true,
        opacity: 0.7,
        font_face: "Microsoft YaHei",
        font_size_ratio: 0.04,
        duration_marquee: 12.0,
        duration_still: 5.0,
        display_region: 0.3,
        block_top: false,
        block_bottom: true,
        block_scroll: false,
        block_keywords: "前排, 打卡",
        outline: 2.0
    })

    readonly property var subtitleCfg: ({
        enabled: true,
        font_face: "Microsoft YaHei",
        font_size_ratio: 0.045,
        opacity: 1.0,
        color: "#FFFFFF",
        back_color: "#000000",
        border_style: "box",
        outline: 2.0,
        alignment: "bottom-center",
        position_ratio: 0.06
    })

    readonly property var sponsorCfg: ({
        categories: [
            { key: "sponsor", name: "赞助/恰饭", desc: "付费推广、推荐和直接广告", color: "#00D400", tier: "auto" },
            { key: "selfpromo", name: "无偿/自我推广", desc: "含有商品、捐赠或合作者信息", color: "#FFFF00", tier: "auto" },
            { key: "exclusive_access", name: "独家访问/抢先体验", desc: "整段视频标记", color: "#00C000", tier: "bar" },
            { key: "interaction", name: "三连/互动提醒", desc: "提醒观众一键三连或关注", color: "#FF00FF", tier: "manual" },
            { key: "poi_highlight", name: "精彩时刻/重点", desc: "大部分人寻找的空降时间点", color: "#FF0080", tier: "manual" },
            { key: "intro", name: "过场/开场动画", desc: "没有实际内容的间隔片段", color: "#00FFFF", tier: "manual" },
            { key: "outro", name: "鸣谢/结束画面", desc: "致谢画面或片尾画面", color: "#0000FF", tier: "manual" },
            { key: "preview", name: "回顾/概要", desc: "展示将出现的画面集锦", color: "#0080FF", tier: "bar" },
            { key: "filler", name: "填充内容/前黑/后黑", desc: "纯填充的黑屏或无关画面", color: "#404040", tier: "auto" },
            { key: "music_offtopic", name: "音乐:非音乐部分", desc: "离题片段", color: "#FF8000", tier: "auto" },
            { key: "padding", name: "片尾填充", desc: "片头片尾无意义的填充", color: "#606060", tier: "auto" }
        ],
        notify: true,
        undo_seconds: 3.0,
        min_duration: 1.0
    })

    readonly property var accountCfg: ({
        configured: true,
        masked: "bf3a****（共 42 字符）"
    })

    readonly property var meta: ({
        alignments: [
            { key: "bottom-left", label: "底部 左" }, { key: "bottom-center", label: "底部 中" },
            { key: "bottom-right", label: "底部 右" }, { key: "middle-left", label: "中部 左" },
            { key: "middle-center", label: "中部 中" }, { key: "middle-right", label: "中部 右" },
            { key: "top-left", label: "顶部 左" }, { key: "top-center", label: "顶部 中" },
            { key: "top-right", label: "顶部 右" }
        ],
        tiers: [
            { key: "auto", label: "自动跳过" }, { key: "manual", label: "手动跳过" },
            { key: "bar", label: "仅在进度条显示" }, { key: "prohibited", label: "禁用" }
        ],
        quality: [
            { key: "auto", label: "自动（最高可用）" }, { key: "127", label: "8K" },
            { key: "126", label: "杜比视界" }, { key: "125", label: "HDR" },
            { key: "120", label: "4K" }, { key: "116", label: "1080P60" },
            { key: "112", label: "1080P+" }, { key: "80", label: "1080P" },
            { key: "64", label: "720P" }, { key: "32", label: "480P" },
            { key: "16", label: "360P" }
        ],
        autostart: [
            { key: "none", label: "禁止自启" }, { key: "registry", label: "开启自启" }
        ],
        theme: [
            { key: "dark", label: "深色模式" }, { key: "light", label: "浅色模式" },
            { key: "system", label: "跟随系统" }
        ]
    })
}
