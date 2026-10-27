import QtQuick

// NavigationView 的一行页签：悬停底色 + 当前项左侧强调条
Rectangle {
    id: nav

    required property string pageKey
    required property string label
    property bool current: false

    signal activated()

    height: 38
    radius: SettingsTheme.radius
    color: current ? SettingsTheme.sel : (hover.hovered ? SettingsTheme.hover : "transparent")

    Behavior on color { ColorAnimation { duration: 120 } }

    Text {
        anchors.verticalCenter: parent.verticalCenter
        anchors.left: parent.left
        anchors.leftMargin: 16
        anchors.right: parent.right
        anchors.rightMargin: 8
        text: nav.label
        color: SettingsTheme.text
        font.pixelSize: SettingsTheme.bodySize
        elide: Text.ElideRight
    }

    // 选中指示条（Fluent NavigationView 的三像素强调条）
    Rectangle {
        anchors.left: parent.left
        anchors.leftMargin: 3
        anchors.verticalCenter: parent.verticalCenter
        width: 3
        height: nav.current ? 16 : 0
        radius: 1.5
        color: SettingsTheme.accent
        Behavior on height { NumberAnimation { duration: 130; easing.type: Easing.OutCubic } }
    }

    HoverHandler { id: hover }

    TapHandler {
        onTapped: nav.activated()
    }
}
