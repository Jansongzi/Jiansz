# -*- coding: utf-8 -*-
from __future__ import annotations

import sys

from core import _write_startup_error

try:
    from ui.web_app import run_app
except Exception as exc:
    _write_startup_error(exc)
    print("启动失败，错误已写入 startup_error.log：", exc)
    raise


def main() -> int:
    args = sys.argv[1:]
    if not args:
        return run_app()
    if args in (["--start-daily"], ["--start"]):
        return run_app(start_daily=True, agent_host=True)
    if args == ["--agent-host"]:
        return run_app(agent_host=True)
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        _write_startup_error(exc)
        print("程序异常退出，错误已写入 startup_error.log：", exc)
        raise
