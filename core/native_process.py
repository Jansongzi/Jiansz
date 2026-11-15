"""启动独立打包程序时隔离当前 Qt 和 PyInstaller 的运行环境。"""
import ctypes
import os
import subprocess
import sys
from threading import Lock


_launch_lock = Lock()


def native_popen(command, **kwargs):
    environment = os.environ.copy()
    for key in list(environment):
        if key.startswith(("QT_", "QML", "_PYI_")) or key in {"PYTHONHOME", "PYTHONPATH"}:
            environment.pop(key, None)
    environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    with _launch_lock:
        previous = ctypes.create_unicode_buffer(32768)
        frozen = getattr(sys, "frozen", False)
        kernel = ctypes.windll.kernel32
        if frozen:
            kernel.GetDllDirectoryW(len(previous), previous)
            kernel.SetDllDirectoryW(None)
        try:
            return subprocess.Popen(command, env=environment, **kwargs)
        finally:
            if frozen:
                kernel.SetDllDirectoryW(previous.value or None)
