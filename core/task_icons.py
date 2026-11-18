"""读取任务实际启动的 Windows 程序图标，无需运行程序。"""
from base64 import b64encode
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

import pywintypes
import win32gui
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage

from core.core import resolve_task_command


def task_icon(script_path: str, args: str) -> str:
    if not script_path or script_path.startswith(("\\\\", "//")):
        return ""
    try:
        task = SimpleNamespace(script_path=script_path, args=args, use_args=bool(args))
        executable, _ = resolve_task_command(task)
        if str(executable).startswith("\\\\") or executable.suffix.casefold() != ".exe" or not executable.is_file():
            return ""
        stat = executable.stat()
        return _extract_icon(executable, stat.st_mtime_ns, stat.st_size)
    except (OSError, ValueError, pywintypes.error, AttributeError):
        return ""


@lru_cache(maxsize=128)
def _extract_icon(executable: Path, _modified: int, _size: int) -> str:
    large, small = win32gui.ExtractIconEx(str(executable), 0, 1)
    try:
        handles = large or small
        if not handles or not handles[0]:
            return ""
        image = QImage.fromHICON(handles[0])
        buffer = QBuffer()
        if image.isNull() or not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not image.save(buffer, "PNG"):
            return ""
        return "data:image/png;base64," + b64encode(bytes(buffer.data())).decode("ascii")
    finally:
        for handle in set(large + small):
            if handle:
                win32gui.DestroyIcon(handle)
