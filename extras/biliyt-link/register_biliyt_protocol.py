#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
register_biliyt_protocol.py — 一键注册 / 注销 biliyt:// 自定义协议（Windows）

比手动改 .reg 更方便：自动查找 BiliYTPlayer.exe，或手动指定路径。

用法：
    python register_biliyt_protocol.py                          # 自动找 exe，写入 HKCU（免管理员，推荐）
    python register_biliyt_protocol.py "D:/xxx/BiliYTPlayer.exe" # 手动指定 exe 路径
    python register_biliyt_protocol.py --hkcr                    # 写入 HKCR（全局，需管理员）
    python register_biliyt_protocol.py --unregister              # 注销协议
    python register_biliyt_protocol.py --dry-run                 # 只预览将写入的注册表项，不实际写入
"""

import os
import sys
import ctypes
import winreg

PROTOCOL = "biliyt"

# 候选 exe 位置（按优先级），找不到时请手动传入路径
# 主方案 = 轻量转发器 biliyt-forwarder.exe（写剪贴板交给运行中的播放器）
# 备选：手动传入 exe 路径
CANDIDATE_PATHS = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "biliyt-forwarder.exe"),
    r"C:/Program Files/BiliYTPlayer/biliyt-forwarder.exe",
    r"C:/Program Files (x86)/BiliYTPlayer/biliyt-forwarder.exe",
]


def find_exe():
    for p in CANDIDATE_PATHS:
        if os.path.isfile(p):
            return p
    return None


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def register(exe_path, hkcr=False, dry_run=False):
    """写入协议注册表。hkcr=False 用 HKCU\\Software\\Classes（免管理员）。"""
    if not os.path.isfile(exe_path):
        print(f"[!] 找不到 exe：{exe_path}")
        return False

    if hkcr:
        root = winreg.HKEY_CLASSES_ROOT
        base = PROTOCOL
    else:
        root = winreg.HKEY_CURRENT_USER
        base = "Software\\Classes\\" + PROTOCOL

    # 需要写两个键：
    #   <base>\                    默认值 = URL:BiliYTPlayer Protocol，值 URL Protocol = ""
    #   <base>\shell\open\command  默认值 = "exe" "%1"
    command = f'"{exe_path}" "%1"'
    entries = [
        (base, "URL:BiliYTPlayer Protocol", "URL Protocol", ""),
        (base + r"\shell\open\command", command, None, None),
    ]

    if dry_run:
        print(f"[dry-run] 将写入 {('HKCR' if hkcr else 'HKCU\\Software\\Classes')} 下的 {base}")
        for key, default, vname, vval in entries:
            print(f"  [{key}]  @ = {default!r}" + (f"  {vname} = {vval!r}" if vname else ""))
        return True

    try:
        for key, default, vname, vval in entries:
            with winreg.CreateKeyEx(root, key, 0, winreg.KEY_SET_VALUE) as k:
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ, default)
                if vname is not None:
                    winreg.SetValueEx(k, vname, 0, winreg.REG_SZ, vval)
        print(f"[+] 已注册 {PROTOCOL}:// 协议 → {exe_path}")
        print(f"    注册位置：{'HKCR（全局）' if hkcr else 'HKCU\\Software\\Classes（当前用户）'}")
        return True
    except PermissionError:
        print("[!] 权限不足。写入 HKCR 需要管理员；写入 HKCU 一般不需要。")
        print("    提示：右键「以管理员身份运行」终端后重试，或改用 --dry-run 后手动导 .reg。")
        return False


def unregister(dry_run=False):
    """从 HKCU 和 HKCR 两处都尝试删除（有一处成功即视为已注销）。"""
    targets = [
        (winreg.HKEY_CURRENT_USER, "Software\\Classes\\" + PROTOCOL),
        (winreg.HKEY_CLASSES_ROOT, PROTOCOL),
    ]
    removed = False
    for root, base in targets:
        try:
            if dry_run:
                print(f"[dry-run] 将删除 [{base}]")
                removed = True
                continue
            winreg.DeleteKey(root, base + r"\shell\open\command")
            winreg.DeleteKey(root, base + r"\shell\open")
            winreg.DeleteKey(root, base + r"\shell")
            winreg.DeleteKey(root, base)
            print(f"[+] 已删除 [{base}]")
            removed = True
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"[!] 删除 [{base}] 失败：{e}")
    if removed:
        print("[+] 注销完成。")
    else:
        print("[*] 未找到已注册的协议，无需注销。")
    return True


def main():
    args = sys.argv[1:]
    hkcr = "--hkcr" in args
    dry_run = "--dry-run" in args
    if "--unregister" in args:
        unregister(dry_run)
        return

    # 第一个非 -- 参数视为 exe 路径
    exe_path = None
    for a in args:
        if not a.startswith("--"):
            exe_path = a
            break
    if not exe_path:
        exe_path = find_exe()
        if not exe_path:
            print("[!] 未自动找到 BiliYTPlayer.exe，请手动传入路径：")
            print("    python register_biliyt_protocol.py \"D:/xxx/BiliYTPlayer.exe\"")
            sys.exit(1)
        print(f"[*] 自动找到 exe：{exe_path}")

    register(exe_path, hkcr=hkcr, dry_run=dry_run)


if __name__ == "__main__":
    main()
