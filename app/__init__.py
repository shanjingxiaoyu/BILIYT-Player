# -*- coding: utf-8 -*-
"""app 包：QML 迁移新增的、与 UI 框架无关的应用层模块。

模块职责：
    app_config   通用设置（关闭行为 / 开机自启 / 画质偏好）读写
    quality      B 站画质档位映射
    autostart    开机自启（注册表 HKCU\\...\\Run）
    tray         托盘图标 + 关闭行为决策
"""
