# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path
from core import _write_startup_error, app_dir

try:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QIcon
    from ui.main_window import MainWindow
except Exception as exc:
    _write_startup_error(exc)
    print("启动失败，错误已写入 startup_error.log：", exc)
    raise


def main() -> int:
    app = QApplication(sys.argv)

    # 设置全局程序图标
    base = app_dir()
    icon_paths = [
        base / "assets" / "icon.ico",
        base / "icon.ico",
        Path(r"C:\Users\Jiansonze\Pictures\图标\s2ryw-u10dx-001.ico"),
    ]
    icon = None
    for p in icon_paths:
        if p.exists():
            icon = QIcon(str(p))
            app.setWindowIcon(icon)
            break

    window = MainWindow()
    if icon and not icon.isNull():
        window.setWindowIcon(icon)
    window.show()
    return app.exec()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        _write_startup_error(exc)
        print("程序异常退出，错误已写入 startup_error.log：", exc)
        raise
