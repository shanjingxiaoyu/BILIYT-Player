# -*- coding: utf-8 -*-
"""版本号与窗口标题的唯一来源。

发版时只改这里：VERSION → 打包 → 发布目录名与 release notes 跟 VERSION 对齐。

⚠️ WINDOW_TITLE 同时是单实例守卫里 FindWindowW 的查找键。窗口标题和守卫
   必须用同一个常量，否则改了标题、守卫还拿旧字符串找窗口，重复启动时
   就没法把已有实例提到前台。
"""
VERSION = "3.2.1"
APP_NAME = "剪贴板直连播放器"
WINDOW_TITLE = f"{APP_NAME} v{VERSION} — B站 / YouTube"
