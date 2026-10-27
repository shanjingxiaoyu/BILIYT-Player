# -*- coding: utf-8 -*-
"""单实例守卫 —— 保证同一时刻只有一个 BiliYTPlayer 在跑。

为什么必须有：程序的核心是「剪贴板监听线程 + 复制链接就开 mpv」。
跑起来两份的话，同一个链接会被两个监听线程各抓一次，于是
**同时开两个 mpv 播同一个视频**，日志区和播放历史也各记一份。

## 为什么用 QLockFile 而不是 QLocalServer 做互斥

实测踩坑：Windows 的命名管道**允许多个同名 server 同时 listen()**（每个
`CreateNamedPipe` 只是增加一个实例），所以 `QLocalServer.listen()` 在已有
实例在跑时**照样返回 True**，用它做单实例锁在 Windows 上完全无效 ——
两个进程都能"成功"拿到锁，然后一起监听剪贴板。

`QLockFile` 才是可靠的那个：
* 基于文件锁（Windows 用 `LockFileEx`，Linux 用 `flock`），系统级互斥
* 自带陈旧检测：进程被 kill 后锁文件里的 PID 已死，下次启动会自动接管
  （不需要人工删文件，也不像 `QSharedMemory` 在 Linux 上被 kill 后假死）
* 记录 PID + 主机名，能判断"占用者是否真的活着"

## QLocalServer / QLocalSocket 仍然保留，但只负责「叫醒已有实例」

用户又点了一次程序图标时，正确的体感是「把已有窗口弹到前台」，
而不是开第二份、也不该只是弹个"已在运行"的对话框就完事。
这部分用本地 socket 传一个字节就够了。
"""

import getpass

from PySide6.QtCore import QLockFile, QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

#: 唤醒消息（内容无所谓，连上就代表"用户又点了一次"）
_MSG_SHOW = b"show"


def _safe_user() -> str:
    """用户名可能含中文/空格，socket 名只允许有限字符，做保守清洗。"""
    try:
        user = getpass.getuser()
    except Exception:
        user = "default"
    return "".join(c for c in user if c.isalnum() or c in "-_") or "default"


def _server_name() -> str:
    """本地 socket 名。带上用户名，避免同机多用户互相顶掉。"""
    return f"BiliYTPlayer-single-{_safe_user()}"


class SingleInstance(QObject):
    """第一个实例持有本对象；后续实例在启动早期就退出。"""

    #: 另一个实例被启动时发出 —— 用来把主窗口弹回前台
    activated = Signal()

    def __init__(self, config_dir, parent=None):
        super().__init__(parent)
        self._name = _server_name()
        self._lock: QLockFile | None = None
        self._server: QLocalServer | None = None

    # ------------------------------------------------------------------
    # 第二个实例：连接已存在的实例并让它显形
    # ------------------------------------------------------------------
    def notify_existing(self, timeout_ms: int = 600) -> bool:
        """连上已有实例并让它显形。返回 True = 确实有实例在跑（调用方应退出）。"""
        sock = QLocalSocket()
        sock.connectToServer(self._name)
        if not sock.waitForConnected(timeout_ms):
            return False
        try:
            sock.write(_MSG_SHOW)
            sock.flush()
            sock.waitForBytesWritten(timeout_ms)
        except Exception:
            pass
        finally:
            try:
                sock.disconnectFromServer()
            except Exception:
                pass
        return True

    # ------------------------------------------------------------------
    # 第一个实例：拿锁 + 占住 socket 名
    # ------------------------------------------------------------------
    def acquire(self) -> bool:
        """成为唯一的实例。返回 False 表示已有实例在跑。"""
        from pathlib import Path

        lock_path = Path(self._lock_path())
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

        lock = QLockFile(str(lock_path))        # 陈旧锁自动接管：进程被 kill 后 PID 已死，Qt 会判定失效并清掉。
        # 给 0 秒等待 = 立即判定，不该让用户等。
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            # 占着锁的那个进程还活着吗？
            pid, hostname, appname = lock.getLockInfo()
            if pid and self._pid_alive(pid, hostname):
                return False
            # 占用者已死但锁没清干净（异常退出/断电）→ 强制接管
            lock.removeStaleLockFile()
            if not lock.tryLock(0):
                return False

        self._lock = lock

        # 占住 socket 名，供后续实例唤醒自己。
        # Windows 下同名 server 能 listen 成功多个实例，所以这里失败与否
        # 只影响"能否被唤醒"，不影响互斥正确性 —— 互斥已由 QLockFile 保证。
        QLocalServer.removeServer(self._name)   # 清掉可能残留的旧 socket
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if self._server.listen(self._name):
            self._server.newConnection.connect(self._on_new_connection)
        else:
            self._server = None
        return True

    @staticmethod
    def _lock_path() -> str:
        """锁文件放临时目录：进程退出后残留也无害（有 PID 陈旧检测）。"""
        import os
        import tempfile
        return os.path.join(tempfile.gettempdir(), f"BiliYTPlayer-{_safe_user()}.lock")

    @staticmethod
    def _pid_alive(pid: int, hostname: str) -> bool:
        """占用者是否还活着。跨主机的情况一律当作活着（保守）。"""
        import socket as _socket
        try:
            if hostname and hostname != _socket.gethostname():
                return True
        except Exception:
            return True
        import sys
        if sys.platform == "win32":
            # Windows 没有 os.kill(pid, 0) 的语义安全版，直接查进程表
            try:
                import ctypes
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                h = ctypes.windll.kernel32.OpenProcess(
                    PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
                if not h:
                    return False
                # 还能拿到退出码说明已经退出了
                code = ctypes.c_ulong()
                ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
                ctypes.windll.kernel32.CloseHandle(h)
                if not ok:
                    return False
                return code.value == 259   # STILL_ACTIVE
            except Exception:
                return True   # 判断不了就当活着，宁可让用户手动删锁
        try:
            import os
            os.kill(int(pid), 0)
            return True
        except (OSError, ValueError):
            return False

    def _on_new_connection(self):
        while self._server and self._server.hasPendingConnections():
            conn = self._server.nextPendingConnection()
            if conn is None:
                break
            try:
                conn.readyRead.connect(lambda c=conn: c.readAll())
                conn.disconnected.connect(conn.deleteLater)
            except Exception:
                pass
            self.activated.emit()

    def release(self):
        """退出时释放锁与 socket 名，让下次启动立刻可用。"""
        if self._server is not None:
            try:
                self._server.close()
            except Exception:
                pass
            self._server = None
        try:
            QLocalServer.removeServer(self._name)
        except Exception:
            pass
        if self._lock is not None:
            try:
                self._lock.unlock()
            except Exception:
                pass
            self._lock = None
