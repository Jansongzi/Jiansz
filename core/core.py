# -*- coding: utf-8 -*-
"""
自动代肝脚本 - 本地桌面脚本调度器

运行方式：
    pip install PySide6
    python main.py

说明：
    1. 本程序只管理自己直接启动的脚本进程。
    2. 默认按表格顺序执行任务。
    3. 每个任务可设置最大运行时间，超时后按 timeout_action 处理。
    4. 配置保存到程序目录下的 config.json。
    5. 日志按每次程序启动单独保存到 logs/YYYY-MM-DD.log、logs/YYYY-MM-DD(2).log。
"""

from __future__ import annotations

import json
import tempfile
import uuid
import os
import platform
import subprocess
import shlex
import ctypes
import ctypes.wintypes as wintypes
import winreg
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import traceback
import re

from core.native_process import native_popen
from core.task_results import TaskLogProbe

# ============================================================
# 启动器快速退出保护
#
# 如果任务配置了“目标进程关键词”或“游戏窗口关键词”，则直接启动的
# 脚本/启动器进程允许很快退出。此时不应立刻判定任务失败，而应继续
# 等待配置的监控目标出现。
# ============================================================

def _v3117_has_external_monitor(task) -> bool:
    try:
        process_keywords = getattr(task, "process_keywords", None)
        window_keywords = getattr(task, "window_keywords", None)
        wait_process_name = getattr(task, "wait_process_name", "")
        if process_keywords or window_keywords or wait_process_name:
            return True
    except Exception:
        pass
    try:
        if task.get("process_keywords") or task.get("window_keywords") or task.get("wait_process_name"):
            return True
    except Exception:
        pass
    return False



try:
    import psutil
except Exception:  # psutil 是可选兜底，打包时 requirements.txt 已包含。
    psutil = None


def _write_startup_error(exc: BaseException) -> None:
    """把启动阶段异常写入文件，避免双击运行时窗口一闪而过看不到报错。"""
    try:
        path = Path.cwd() / "startup_error.log"
        with path.open("a", encoding="utf-8") as f:
            f.write("\n" + "=" * 80 + "\n")
            f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "\n")
            f.write("Python: " + sys.version + "\n")
            f.write("Executable: " + sys.executable + "\n")
            f.write("CWD: " + str(Path.cwd()) + "\n")
            f.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    except Exception:
        pass


try:
    from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
    from PySide6.QtGui import QAction, QTextCursor, QKeySequence, QShortcut, QPixmap, QGuiApplication
    from PySide6.QtWidgets import (
        QApplication,
        QAbstractItemView,
        QCheckBox,
        QComboBox,
        QFileDialog,
        QDialog,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QSpinBox,
        QTableWidget,
        QTableWidgetItem,
        QTextEdit,
        QVBoxLayout,
        QWidget,
        QSplitter,
    )
except Exception as exc:
    _write_startup_error(exc)
    print("启动失败，错误已写入 startup_error.log：", exc)
    if sys.stdin is not None and sys.stdin.isatty():
        input("按回车退出...")
    raise


APP_NAME = "简小松·脚本调度助手 v0.1.1"
CONFIG_FILE_NAME = "config.json"
LOG_DIR_NAME = "logs"
AUTHOR_URL = "https://space.bilibili.com/397555449?spm_id_from=333.1007.0.0"
GITHUB_URL = "https://github.com/Jansongzi"
LOCAL_DOC_RELATIVE_PATH = Path("docs") / "index.html"

WAIT_MODES: Dict[str, str] = {
    "direct_process": "等待直接启动进程",
    "process_name": "等待指定进程名",
    "fire_and_continue": "启动后不等待",
    "window_title": "等待窗口标题出现后消失",
    "cmdline_keyword": "等待命令行出现后消失",
}

CONCURRENT_POLICIES: Dict[str, str] = {
    "wait_all": "等待本组全部完成",
    "wait_first": "只等本组第一个完成",
}

TIMEOUT_ACTIONS: Dict[str, str] = {
    "kill_and_continue": "强制结束并继续",
    "skip_and_continue": "不结束，跳过并继续",
    "stop_all": "停止全部任务",
}
TIMEOUT_ACTION_LABEL_TO_VALUE = {label: value for value, label in TIMEOUT_ACTIONS.items()}


def app_dir() -> Path:
    """
    返回程序所在目录。

    普通脚本运行时：main.py 所在目录。
    PyInstaller 打包后：exe 所在目录。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # core/core.py 位于 <project>/core/core.py，资源、配置、日志都应以项目根目录为准。
    # 旧版返回 core/ 目录，导致 assets/ 放在项目根目录时无法读取。
    return Path(__file__).resolve().parent.parent




def build_session_marker(task_name: str, marker_type: str) -> str:
    """
    V31.11:
    为日志总结器提供 SESSION 隔离标记。
    避免 maaend / okww / m9a 等任务日志串流污染。
    """
    marker_type = marker_type.upper().strip()
    return f"===== SESSION {marker_type} : {task_name} ====="


def now_time_text() -> str:
    return datetime.now().strftime("%H:%M:%S")


def today_text() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def format_seconds(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def safe_int(value: Any, default: int = 0, min_value: Optional[int] = None) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    if min_value is not None:
        result = max(min_value, result)
    return result


def normalize_keywords(value: Any) -> List[str]:
    """把导入配置中的目标进程/游戏窗口关键词统一成字符串列表。

    兼容两种写法：
    1. ["YuanShen.exe", "原神"]
    2. "YuanShen.exe; 原神"
    """
    if value is None:
        return []
    if isinstance(value, list):
        raw_items = value
    else:
        text = str(value).replace("\n", ";").replace("，", ";").replace(",", ";")
        raw_items = text.split(";")
    result: List[str] = []
    for item in raw_items:
        keyword = str(item).strip()
        if keyword and keyword not in result:
            result.append(keyword)
    return result


def keywords_to_text(value: Any) -> str:
    return "; ".join(normalize_keywords(value))


@dataclass

class TaskConfig:
    enabled: bool = True
    name: str = "新任务"
    script_path: str = ""
    order: int = 1
    timeout_minutes: int = 30
    timeout_action: str = "kill_and_continue"
    use_args: bool = False
    args: str = ""
    wait_mode: str = "direct_process"
    wait_process_name: str = ""
    confirm_enter_delay_seconds: int = 0
    concurrent_group: str = ""
    concurrent_policy: str = "wait_all"
    enable_watchdog: bool = False
    process_keywords: List[str] = field(default_factory=list)
    window_keywords: List[str] = field(default_factory=list)
    monitor_absent_grace_seconds: int = 15
    # v31.15：UI 名称改为“目标进程关键词 / 游戏窗口关键词”。
    # 配置键仍保持 process_keywords / window_keywords，兼容旧配置。
    launcher_process: str = ""
    main_process: str = ""
    game_process: str = ""
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    # v31.12：任务级超时截图开关已移除。
    # 超时截图只由 AppConfig.enable_timeout_screenshot 全局控制。

    @classmethod
    def from_dict(cls, data: Dict[str, Any], fallback_order: int) -> "TaskConfig":
        try:
            task_id = uuid.UUID(str(data.get("task_id", ""))).hex
        except ValueError:
            legacy_identity = json.dumps([fallback_order, data.get("name"), data.get("script_path")],
                                         ensure_ascii=False)
            task_id = uuid.uuid5(uuid.NAMESPACE_OID, "jiansz:" + legacy_identity).hex

        action = str(data.get("timeout_action", "kill_and_continue"))
        if action not in TIMEOUT_ACTIONS:
            action = "kill_and_continue"

        wait_mode = str(data.get("wait_mode", "direct_process"))
        if wait_mode not in WAIT_MODES:
            wait_mode = "direct_process"

        concurrent_policy = str(data.get("concurrent_policy", "wait_all"))
        if concurrent_policy not in CONCURRENT_POLICIES:
            concurrent_policy = "wait_all"

        return cls(
            task_id=task_id,
            enabled=bool(data.get("enabled", True)),
            name=str(data.get("name", f"任务{fallback_order}")),
            script_path=str(data.get("script_path", "")),
            order=safe_int(data.get("order", fallback_order), fallback_order, 1),
            timeout_minutes=safe_int(data.get("timeout_minutes", 30), 30, 0),
            timeout_action=action,
            use_args=bool(data.get("use_args", False)),
            args=str(data.get("args", "")),
            wait_mode=wait_mode,
            wait_process_name=str(data.get("wait_process_name", "")),
            confirm_enter_delay_seconds=safe_int(data.get("confirm_enter_delay_seconds", 0), 0, 0),
            concurrent_group=str(data.get("concurrent_group", "")).strip(),
            concurrent_policy=concurrent_policy,
            enable_watchdog=bool(data.get("enable_watchdog", False)),
            process_keywords=normalize_keywords(data.get("process_keywords", [])),
            window_keywords=normalize_keywords(data.get("window_keywords", data.get("game_window_keywords", []))),
            monitor_absent_grace_seconds=min(3600, safe_int(data.get("monitor_absent_grace_seconds", 15), 15, 1)),
            launcher_process=str(data.get("launcher_process", data.get("start_process", ""))).strip(),
            main_process=str(data.get("main_process", "")).strip(),
            game_process=str(data.get("game_process", "")).strip(),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "enabled": self.enabled,
            "name": self.name,
            "script_path": self.script_path,
            "order": self.order,
            "timeout_minutes": self.timeout_minutes,
            "timeout_action": self.timeout_action,
            "use_args": self.use_args,
            "args": self.args,
            "wait_mode": self.wait_mode,
            "wait_process_name": self.wait_process_name,
            "confirm_enter_delay_seconds": self.confirm_enter_delay_seconds,
            "concurrent_group": self.concurrent_group,
            "concurrent_policy": self.concurrent_policy,
            "enable_watchdog": self.enable_watchdog,
            "process_keywords": normalize_keywords(self.process_keywords),
            "window_keywords": normalize_keywords(self.window_keywords),
            "monitor_absent_grace_seconds": self.monitor_absent_grace_seconds,
            "launcher_process": self.launcher_process,
            "main_process": self.main_process,
            "game_process": self.game_process,
        }


@dataclass
class AppConfig:
    theme: str = "jiansz"
    shutdown_after_done: bool = False
    shutdown_delay_seconds: int = 60
    auto_exit_after_done: bool = False
    auto_start_tasks: bool = False
    windows_startup: bool = False
    enable_timeout_screenshot: bool = False
    tasks: List[TaskConfig] = field(default_factory=list)
    extra_settings: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppConfig":
        theme = str(data.get("theme", "jiansz")).lower()
        allowed_themes = {"light", "dark", "jiansz", "campus", "fresh", "fantasy"}
        if theme not in allowed_themes:
            theme = "jiansz"

        raw_tasks = data.get("tasks", [])
        tasks: List[TaskConfig] = []
        if isinstance(raw_tasks, list):
            for index, item in enumerate(raw_tasks, start=1):
                if isinstance(item, dict):
                    tasks.append(TaskConfig.from_dict(item, fallback_order=index))

        tasks.sort(key=lambda item: item.order)
        ensure_unique_task_ids(tasks)
        for index, task in enumerate(tasks, start=1):
            task.order = index

        return cls(
            theme=theme,
            shutdown_after_done=bool(data.get("shutdown_after_done", False)),
            shutdown_delay_seconds=safe_int(data.get("shutdown_delay_seconds", 60), 60, 0),
            auto_exit_after_done=bool(data.get("auto_exit_after_done", False)),
            auto_start_tasks=bool(data.get("auto_start_tasks", False)),
            windows_startup=bool(data.get("windows_startup", False)),
            enable_timeout_screenshot=bool(data.get("enable_timeout_screenshot", False)),
            tasks=tasks,
            extra_settings={k: v for k, v in data.items() if k not in {"theme", "shutdown_after_done", "shutdown_delay_seconds", "auto_exit_after_done", "auto_start_tasks", "windows_startup", "enable_timeout_screenshot", "tasks"}},
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            **self.extra_settings,
            "theme": self.theme,
            "shutdown_after_done": self.shutdown_after_done,
            "shutdown_delay_seconds": self.shutdown_delay_seconds,
            "auto_exit_after_done": self.auto_exit_after_done,
            "auto_start_tasks": self.auto_start_tasks,
            "windows_startup": self.windows_startup,
            "enable_timeout_screenshot": self.enable_timeout_screenshot,
            "tasks": [task.to_dict() for task in self.tasks],
        }


def ensure_unique_task_ids(tasks: List[TaskConfig]) -> None:
    seen = set()
    for task in tasks:
        if task.task_id in seen:
            task.task_id = uuid.uuid4().hex
        seen.add(task.task_id)


class ConfigManager:
    def __init__(self, config_path: Path):
        self.config_path = config_path

    def load(self) -> AppConfig:
        if not self.config_path.exists():
            config = AppConfig()
            self.save(config)
            return config

        try:
            with self.config_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("config.json 顶层结构不是对象")
            return AppConfig.from_dict(data)
        except Exception as exc:
            backup_path = self.config_path.with_suffix(".broken.json")
            try:
                self.config_path.replace(backup_path)
            except Exception:
                pass

            config = AppConfig()
            self.save(config)
            print(f"读取配置失败，已创建默认配置：{exc}")
            return config

    def save(self, config: AppConfig) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=self.config_path.parent, delete=False) as f:
            pending = Path(f.name)
            try:
                json.dump(config.to_dict(), f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            except BaseException:
                f.close()
                pending.unlink(missing_ok=True)
                raise
        try:
            pending.replace(self.config_path)
        finally:
            pending.unlink(missing_ok=True)


class FileLogger:
    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        self._lock = threading.Lock()
        self.session_label = self._allocate_session_label()
        self._log_path = self.log_dir / f"{self.session_label}.log"

    def _allocate_session_label(self) -> str:
        """按程序启动批次分配日志名：2026-05-26.log、2026-05-26(2).log。"""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        base = today_text()
        first = self.log_dir / f"{base}.log"
        if not first.exists():
            return base
        index = 2
        while True:
            candidate = self.log_dir / f"{base}({index}).log"
            if not candidate.exists():
                return f"{base}({index})"
            index += 1

    def log_path(self) -> Path:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        return self._log_path

    def write_line(self, line: str) -> None:
        with self._lock:
            path = self.log_path()
            with path.open("a", encoding="utf-8") as f:
                f.write(line.rstrip() + "\n")

    def make_line(self, message: str) -> str:
        return f"[{now_time_text()}] {message}"

    def log(self, message: str) -> str:
        line = self.make_line(message)
        self.write_line(line)
        return line


class RuntimeStatsManager:
    """记录每次执行耗时，并按脚本名称维护历史平均值。"""
    def __init__(self, base_dir: Path):
        self.stats_dir = base_dir / "runtime_stats"
        self.stats_dir.mkdir(parents=True, exist_ok=True)
        self.history_path = self.stats_dir / "runtime_history.json"
        self._lock = threading.Lock()
        self._save_lock = threading.Lock()
        self.records: List[Dict[str, Any]] = []
        self.session_id = self._new_session_id()

    @staticmethod
    def _new_session_id() -> str:
        return datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]

    def add_record(self, task_name: str, elapsed_seconds: int, status: str, wait_mode: str,
                   *, script_path: str = "", result: str = "", source: str = "",
                   task_id: str = "") -> dict:
        with self._lock:
            record = {
                "session_id": self.session_id,
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "task_name": task_name,
                "task_id": task_id,
                "elapsed_seconds": int(max(0, elapsed_seconds)),
                "elapsed_text": format_seconds(int(max(0, elapsed_seconds))),
                "status": status,
                "wait_mode": wait_mode,
                "script_path": script_path,
                "result": result,
                "source": source,
            }
            self.records.append(record)
            return record

    def _load_history(self) -> Dict[str, Any]:
        if not self.history_path.exists():
            return {}
        try:
            with self.history_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def save_session(self) -> List[Path]:
        with self._save_lock:
            return self._save_session()

    def _save_session(self) -> List[Path]:
        with self._lock:
            records = list(self.records)
        if not records:
            return []

        session_json = self.stats_dir / f"session_{self.session_id}.json"
        session_csv = self.stats_dir / f"session_{self.session_id}.csv"
        summary_json = self.stats_dir / "runtime_summary.json"

        with session_json.open("w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)

        with session_csv.open("w", encoding="utf-8-sig") as f:
            f.write("session_id,time,task_name,elapsed_seconds,elapsed_text,status,wait_mode\n")
            for r in records:
                def esc(v: Any) -> str:
                    text = str(v).replace('"', '""')
                    return f'"{text}"'
                f.write(",".join([
                    esc(r.get("session_id", "")), esc(r.get("time", "")), esc(r.get("task_name", "")),
                    str(r.get("elapsed_seconds", 0)), esc(r.get("elapsed_text", "")),
                    esc(r.get("status", "")), esc(r.get("wait_mode", "")),
                ]) + "\n")

        history = self._load_history()
        for r in records:
            name = str(r.get("task_name", "未命名"))
            elapsed = int(r.get("elapsed_seconds", 0))
            item = history.get(name, {"count": 0, "total_seconds": 0, "average_seconds": 0, "last_seconds": 0})
            item["count"] = int(item.get("count", 0)) + 1
            item["total_seconds"] = int(item.get("total_seconds", 0)) + elapsed
            item["average_seconds"] = round(item["total_seconds"] / max(1, item["count"]), 2)
            item["average_text"] = format_seconds(int(item["average_seconds"]))
            item["last_seconds"] = elapsed
            item["last_text"] = format_seconds(elapsed)
            item["last_time"] = r.get("time", "")
            item["last_status"] = r.get("status", "")
            history[name] = item

        with self.history_path.open("w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        with summary_json.open("w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        with self._lock:
            del self.records[:len(records)]
            self.session_id = self._new_session_id()
        return [session_json, session_csv, self.history_path, summary_json]



class ElevatedProcessAdapter:
    """用 ShellExecuteEx(runas) 启动的提权进程适配器。

    只实现调度器需要的 poll / wait / terminate / pid / returncode，
    让管理员子进程能尽量沿用原来的监控逻辑。
    """

    def __init__(self, h_process: int, pid: int):
        self.h_process = h_process
        self.pid = int(pid)
        self.returncode: Optional[int] = None

    def poll(self) -> Optional[int]:
        if self.returncode is not None:
            return self.returncode
        WAIT_OBJECT_0 = 0
        WAIT_TIMEOUT = 0x00000102
        result = ctypes.windll.kernel32.WaitForSingleObject(wintypes.HANDLE(self.h_process), 0)
        if result == WAIT_TIMEOUT:
            return None
        if result == WAIT_OBJECT_0:
            code = wintypes.DWORD()
            if ctypes.windll.kernel32.GetExitCodeProcess(wintypes.HANDLE(self.h_process), ctypes.byref(code)):
                self.returncode = int(code.value)
            else:
                self.returncode = 0
            try:
                ctypes.windll.kernel32.CloseHandle(wintypes.HANDLE(self.h_process))
            except Exception:
                pass
            return self.returncode
        return None

    def wait(self, timeout: Optional[float] = None) -> int:
        start = time.monotonic()
        while True:
            code = self.poll()
            if code is not None:
                return code
            if timeout is not None and time.monotonic() - start >= timeout:
                raise subprocess.TimeoutExpired(str(self.pid), timeout)
            time.sleep(0.1)

    def terminate(self) -> None:
        try:
            subprocess.Popen(
                ["taskkill", "/PID", str(self.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
            ).wait(timeout=10)
        except Exception:
            try:
                ctypes.windll.kernel32.TerminateProcess(wintypes.HANDLE(self.h_process), 1)
            except Exception:
                pass

    def kill(self) -> None:
        self.terminate()


def resolve_task_command(task, script_path=None):
    """解析任务的实际脚本路径及参数，供启动和更新共同使用。"""
    configured = str(task.script_path or getattr(task, "name", "")).strip()
    path = (script_path or Path(configured).expanduser()).resolve()
    text = str(getattr(task, "args", "")).strip() if getattr(task, "use_args", False) else ""
    args = [part.strip('"') for part in shlex.split(text, posix=False)] if text else []
    if args:
        first = Path(args[0]).expanduser()
        if first.is_absolute() or args[0].startswith(("./", ".\\")) or first.suffix.lower() in {".exe", ".bat", ".cmd", ".py"}:
            return (path.parent / first).resolve(), args[1:]
    return path, args


class ScriptRunnerWorker(QObject):
    log_signal = Signal(str)
    status_signal = Signal(str)
    progress_signal = Signal(int, int)
    task_started_signal = Signal(str, int, int)
    elapsed_signal = Signal(int)
    finished_signal = Signal(bool)  # bool: stopped_by_user_or_error
    buttons_unlock_signal = Signal()
    shutdown_prompt_signal = Signal(int)
    task_error_signal = Signal(str)
    task_launch_success_signal = Signal(str)
    task_report_signal = Signal(object)

    def __init__(
        self,
        tasks: List[TaskConfig],
        shutdown_after_done: bool,
        shutdown_delay_seconds: int,
        logger: FileLogger,
        stats_manager: RuntimeStatsManager,
        enable_timeout_screenshot: bool = False,
    ):
        super().__init__()
        self.tasks = tasks
        self.shutdown_after_done = shutdown_after_done
        self.shutdown_delay_seconds = max(0, int(shutdown_delay_seconds))
        self.enable_timeout_screenshot = bool(enable_timeout_screenshot)
        self.logger = logger
        self.stats_manager = stats_manager
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.current_processes: List[subprocess.Popen[Any]] = []
        self.current_process_names_by_pid: Dict[int, List[str]] = {}
        self.current_tasks_by_pid: Dict[int, TaskConfig] = {}
        # 记录当前任务进程，用于停止执行/紧急停止等明确操作。
        self._current_lock = threading.Lock()
        self._launch_lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self._pause_lock = threading.Lock()
        self._paused_since = None
        self._paused_seconds = 0.0
        self._owned_children = {}
        self._owned_roots = {}
        self._group_threads = []
        self.had_task_error = False
        self.had_unverified_result = False
        self.abnormal_events: List[Dict[str, Any]] = []
        self.abnormal_report_path = self.logger.log_dir / "last_abnormal_report.json"

    def mark_task_error(self, message: str, task_name: str = "") -> None:
        """记录任务级异常，并通知简小松界面显示错误状态。"""
        self._record_task_issue(message, task_name, severity="error", notify_error=True)

    def mark_task_warning(self, message: str, task_name: str = "") -> None:
        """记录任务级提醒。提醒会进入下次运行报告，但不触发 error 状态。"""
        self._record_task_issue(message, task_name, severity="warning", notify_error=False)

    def _record_task_issue(self, message: str, task_name: str = "", *, severity: str = "error", notify_error: bool = True) -> None:
        if severity == "error":
            self.had_task_error = True
        event = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "task_name": task_name or self._guess_task_name_from_message(message),
            "message": message,
            "severity": severity,
            "log_file": self.logger.log_path().name,
            "log_session": getattr(self.logger, "session_label", ""),
        }
        self.abnormal_events.append(event)
        if notify_error:
            self.task_error_signal.emit(message)

    def _guess_task_name_from_message(self, message: str) -> str:
        marker = "任务「"
        if marker in message:
            try:
                return message.split(marker, 1)[1].split("」", 1)[0]
            except Exception:
                return ""
        return ""

    def _save_abnormal_report(self) -> None:
        """保存/清理上次异常报告。"""
        try:
            self.abnormal_report_path.parent.mkdir(parents=True, exist_ok=True)
            if not self.abnormal_events:
                if self.abnormal_report_path.exists():
                    self.abnormal_report_path.unlink()
                return
            error_count = sum(1 for item in self.abnormal_events if item.get("severity", "error") == "error")
            warning_count = sum(1 for item in self.abnormal_events if item.get("severity") == "warning")
            payload = {
                "version": "v0.1.1",
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "log_file": self.logger.log_path().name,
                "log_session": getattr(self.logger, "session_label", ""),
                "summary": f"上次运行发现 {error_count} 个异常项，{warning_count} 个提醒项。",
                "events": self.abnormal_events,
            }
            with self.abnormal_report_path.open("w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            self.log(f"保存异常报告失败：{exc}")

    def log(self, message: str) -> None:
        line = self.logger.log(message)
        self.log_signal.emit(line)

    def request_pause(self) -> None:
        with self._pause_lock:
            if self.pause_event.is_set() or self.stop_event.is_set():
                return
            self._paused_since = time.monotonic()
            self.pause_event.set()
        self.status_signal.emit("已暂停")
        self.log("已暂停调度和超时计时。外部脚本会继续运行。")

    def _clear_pause(self) -> bool:
        with self._pause_lock:
            if self._paused_since is None:
                return False
            self._paused_seconds += time.monotonic() - self._paused_since
            self._paused_since = None
            self.pause_event.clear()
            return True

    def _active_time(self) -> float:
        with self._pause_lock:
            now = time.monotonic()
            pending = now - self._paused_since if self._paused_since is not None else 0.0
            return now - self._paused_seconds - pending

    def request_resume(self) -> None:
        if self._clear_pause():
            self.status_signal.emit("运行中")
            self.log("已恢复调度。")

    def _wait_if_paused(self) -> None:
        while self.pause_event.is_set() and not self.stop_event.is_set():
            time.sleep(0.25)

    @Slot()
    def run(self) -> None:
        stopped = False
        try:
            self.status_signal.emit("运行中")

            enabled_tasks = [task for task in self.tasks if task.enabled]
            enabled_tasks.sort(key=lambda task: task.order)

            total = len(enabled_tasks)
            if total == 0:
                self.log("没有启用的任务，执行结束。")
                self.status_signal.emit("已完成")
                self.progress_signal.emit(0, 0)
                self.finished_signal.emit(False)
                return

            self.log(f"运行日志文件：{self.logger.log_path().name}")
            self.log(f"开始执行任务队列，共 {total} 个启用任务。")
            self.progress_signal.emit(0, total)

            index = 0
            while index < total:
                self._wait_if_paused()
                if self.stop_event.is_set():
                    stopped = True
                    self.log("检测到停止请求，后续任务不再执行。")
                    break

                task = enabled_tasks[index]
                group_name = task.concurrent_group.strip()

                if group_name:
                    group_tasks = [task]
                    j = index + 1
                    while j < total and enabled_tasks[j].concurrent_group.strip() == group_name:
                        group_tasks.append(enabled_tasks[j])
                        j += 1

                    self.progress_signal.emit(index + 1, total)
                    self.status_signal.emit("并发运行中")
                    self.elapsed_signal.emit(0)
                    result = self._run_concurrent_group(group_name, group_tasks, index + 1, total)
                    index = j
                else:
                    self.progress_signal.emit(index + 1, total)
                    self.task_started_signal.emit(task.name, index + 1, total)
                    self.status_signal.emit("运行中")
                    self.elapsed_signal.emit(0)
                    result = self._run_one_task(task)
                    index += 1

                if result in {"stop_requested", "stop_all"}:
                    stopped = True
                    break

            if stopped or self.stop_event.is_set():
                self._stop_owned_processes("停止执行")
            for thread in self._group_threads:
                thread.join()

            if stopped or self.stop_event.is_set():
                self.status_signal.emit("已停止")
                self.log("任务队列已停止。")
                self._save_abnormal_report()
                self.stats_manager.save_session()
                self.finished_signal.emit(True)
                return

            if self.had_task_error:
                self.status_signal.emit("已完成（有异常）")
                self.progress_signal.emit(total, total)
                self.log("任务队列执行结束，但存在启动失败、过早退出、超时强退等异常。")
                self.log("异常已写入上次运行报告；下次启动会主动汇报。")
            elif self.abnormal_events:
                self.status_signal.emit("已完成（有提醒）")
                self.progress_signal.emit(total, total)
                self.log("任务队列执行结束，存在配置或监控目标提醒，但未判定为严重异常。")
                self.log("提醒已写入上次运行报告；下次启动会主动汇报。")
            else:
                self.status_signal.emit("已完成")
                self.progress_signal.emit(total, total)
                self.log("所有任务执行完成。")

            self._save_abnormal_report()

            paths = self.stats_manager.save_session()
            if paths:
                self.log(f"本次运行耗时统计已保存：{paths[0].parent}")
            if (self.shutdown_after_done and not self.had_task_error
                    and not self.had_unverified_result and not self.stop_event.is_set()):
                delay = max(1, self.shutdown_delay_seconds)
                self.shutdown_prompt_signal.emit(delay)
            self.finished_signal.emit(self.had_task_error)

        except Exception as exc:
            self.stop_event.set()
            self._clear_pause()
            self._stop_owned_processes("执行器异常")
            for thread in self._group_threads:
                thread.join()
            self.status_signal.emit("异常")
            self.log(f"执行器发生异常：{exc}")
            self.mark_task_error(f"执行器发生异常：{exc}")
            self._save_abnormal_report()
            try:
                self.stats_manager.save_session()
            except Exception as stats_error:
                self.log(f"保存运行统计失败：{stats_error}")
            finally:
                self.finished_signal.emit(True)

    def _run_concurrent_group(self, group_name: str, group_tasks: List[TaskConfig], start_index: int, total: int) -> str:
        policy = group_tasks[0].concurrent_policy if group_tasks[0].concurrent_policy in CONCURRENT_POLICIES else "wait_all"
        self.log(f"开始并发组「{group_name}」，共 {len(group_tasks)} 个任务，策略：{CONCURRENT_POLICIES.get(policy, policy)}。")
        self.task_started_signal.emit(f"并发组：{group_name}", start_index, total)

        results: Dict[int, str] = {}
        lock = threading.Lock()
        done_events = [threading.Event() for _ in group_tasks]

        def worker(task_index: int, task: TaskConfig) -> None:
            result = "continue"
            try:
                result = self._run_one_task(task)
                if result == "stop_all":
                    self.stop_event.set()
                    self._clear_pause()
            except Exception as exc:
                self.mark_task_error(f"任务「{task.name}」并发执行失败：{exc}", task.name)
                with self._current_lock:
                    processes = [process for process in self.current_processes
                                 if self.current_tasks_by_pid.get(process.pid) is task]
                self._cleanup_processes_for_tasks([task], processes, scope_label="并发异常")
            finally:
                with lock:
                    results[task_index] = result
                done_events[task_index].set()

        threads: List[threading.Thread] = []
        for i, task in enumerate(group_tasks):
            thread = threading.Thread(target=worker, args=(i, task), daemon=True)
            threads.append(thread)
            self._group_threads.append(thread)
            thread.start()
            if self.stop_event.wait(0.2):
                return "stop_requested"

        if policy == "wait_first":
            self.log(f"并发组「{group_name}」采用只等待组首任务策略；组首任务完成后会继续后续队列，其它同组任务可能仍在运行。")
            while not done_events[0].is_set():
                self._wait_if_paused()
                if self.stop_event.is_set():
                    return "stop_requested"
                time.sleep(0.25)
            result = results.get(0, "continue")
            self.log(f"并发组「{group_name}」组首任务已完成，继续后续队列。")
            return result

        while True:
            self._wait_if_paused()
            if self.stop_event.is_set():
                return "stop_requested"
            if all(event.is_set() for event in done_events):
                break
            time.sleep(0.25)

        if any(result in {"stop_requested", "stop_all"} for result in results.values()):
            return "stop_all"
        self.log(f"并发组「{group_name}」全部任务已完成。")
        return "continue"

    def _add_current_process(
        self,
        process: subprocess.Popen[Any],
        process_names: Optional[List[str]] = None,
        task: Optional[TaskConfig] = None,
    ) -> None:
        with self._current_lock:
            self.current_processes.append(process)
            names = []
            for item in process_names or []:
                name = str(item).strip()
                if name and name.lower() not in [n.lower() for n in names]:
                    names.append(name)
            pid = int(getattr(process, "pid", 0) or 0)
            if names:
                self.current_process_names_by_pid[pid] = names
            if task is not None:
                self.current_tasks_by_pid[pid] = task

    def _remove_current_process(self, process: subprocess.Popen[Any]) -> None:
        with self._current_lock:
            self.current_processes = [p for p in self.current_processes if p is not process]
            pid = int(getattr(process, "pid", 0) or 0)
            self.current_process_names_by_pid.pop(pid, None)
            self.current_tasks_by_pid.pop(pid, None)

    def _add_unique_text(self, result: List[str], value: Any) -> None:
        text = str(value or "").strip()
        if text and text.lower() not in [item.lower() for item in result]:
            result.append(text)

    def _process_name_candidates(self, name: str) -> List[str]:
        """把用户填写的进程名转成 taskkill /IM 可尝试的名称。

        兼容 okww / okww.exe / ok-ww.33golbal 这几类写法。
        非 exe 关键词还会交给 psutil 兜底按名称/命令行关键词查杀。
        """
        raw = str(name or "").strip()
        if not raw:
            return []
        candidates: List[str] = []
        self._add_unique_text(candidates, Path(raw).name)
        if not raw.lower().endswith(".exe"):
            self._add_unique_text(candidates, Path(raw).name + ".exe")
        return candidates

    def _task_cleanup_plan(self, task: TaskConfig, script_path: Optional[Path] = None) -> List[tuple[str, List[str]]]:
        """V31.15：统一停止/紧急停止/超时处理的清理顺序。

        顺序固定为：
        1. 启动进程：脚本路径 exe + launcher_process
        2. 目标进程：wait_process_name + main_process + 目标进程关键词(process_keywords)
        3. 游戏/扩展进程：game_process

        说明：
        - process_keywords 在 UI 中显示为“目标进程关键词”，表示脚本拉起/等待/需要清理的目标程序，参与等待判断，也参与超时/停止/紧急停止清理。
        - window_keywords 在 UI 中显示为“游戏窗口关键词”，主要用于窗口存在判断和窗口 PID 兜底清理；
          若用户填写 xxx.exe，则额外兼容为进程名清理，覆盖 endfield.exe 这类旧填法。
        """
        launcher_names: List[str] = []
        main_names: List[str] = []
        game_names: List[str] = []

        if script_path is not None and script_path.suffix.lower() == ".exe":
            self._add_unique_text(launcher_names, script_path.name)
        self._add_unique_text(launcher_names, getattr(task, "launcher_process", ""))

        self._add_unique_text(main_names, task.wait_process_name)
        self._add_unique_text(main_names, getattr(task, "main_process", ""))
        for item in normalize_keywords(task.process_keywords):
            self._add_unique_text(main_names, item)

        self._add_unique_text(game_names, getattr(task, "game_process", ""))
        # 兼容旧用法：如果“游戏窗口关键词”里填了 endfield.exe 这类真实进程名，
        # 清理时也把它当作游戏/扩展进程尝试 taskkill。
        # 注意：不会把 xxx.exe 自动截成 xxx 去匹配窗口；xxx.exe 只代表额外按进程名清理。
        for item in normalize_keywords(task.window_keywords):
            if str(item).strip().lower().endswith(".exe"):
                self._add_unique_text(game_names, item)

        plan: List[tuple[str, List[str]]] = []
        if launcher_names:
            plan.append(("启动脚本进程", launcher_names))
        if main_names:
            plan.append(("目标进程", main_names))
        if game_names:
            plan.append(("游戏/扩展进程", game_names))
        return plan

    def _process_names_for_task(self, task: TaskConfig, script_path: Optional[Path] = None) -> List[str]:
        names: List[str] = []
        for _stage, items in self._task_cleanup_plan(task, script_path):
            for item in items:
                for candidate in self._process_name_candidates(item):
                    self._add_unique_text(names, candidate)
        return names

    def _current_tracked_process_names(self) -> List[str]:
        with self._current_lock:
            raw = [name for names in self.current_process_names_by_pid.values() for name in names]
        result: List[str] = []
        for name in raw:
            self._add_unique_text(result, name)
        return result

    def _current_tracked_tasks(self) -> List[TaskConfig]:
        with self._current_lock:
            raw = list(self.current_tasks_by_pid.values())
        result: List[TaskConfig] = []
        seen: set[int] = set()
        for task in raw:
            ident = id(task)
            if ident not in seen:
                seen.add(ident)
                result.append(task)
        return result

    def request_stop(self) -> None:
        self.stop_event.set()
        self._clear_pause()
        self._stop_owned_processes("停止执行")

    def request_emergency_stop(self) -> None:
        self.stop_event.set()
        self._clear_pause()
        self._stop_owned_processes("紧急停止")

    def _stop_owned_processes(self, label: str) -> None:
        # 和进程启动互斥，避免停止请求与新进程登记交错。
        with self._launch_lock, self._current_lock:
            processes = list(self._owned_roots.values())
        self._cleanup_processes_for_tasks([], processes, scope_label=label)

    def _track_children(self, process) -> None:
        if psutil is None:
            return
        pid = process.pid
        with self._current_lock:
            owned = self._owned_children.setdefault(pid, {})
            candidates = list(owned.values())
            try:
                if process.poll() is None:
                    candidates.append(psutil.Process(pid))
            except (psutil.Error, OSError):
                pass
            for parent in candidates:
                try:
                    if parent.is_running():
                        for child in parent.children(recursive=True):
                            owned.setdefault(child.pid, child)
                except (psutil.Error, OSError):
                    continue

    def _cleanup_processes_for_tasks(self, tasks, direct_processes=None,
                                     scope_label="进程清理", include_window_keywords=False) -> None:
        # 只结束本轮登记的进程及已确认的子进程。关键词用于完成判定。
        with self._cleanup_lock:
            for process in direct_processes or []:
                if self._owned_roots.get(process.pid) is not process:
                    continue
                self._track_children(process)
                with self._current_lock:
                    children = list(self._owned_children.get(process.pid, {}).values())
                self.log(f"{scope_label}：清理本轮启动的 PID {process.pid} 及已登记的子进程。")
                if process.poll() is None:
                    self._terminate_process(process)
                remaining = {}
                for child in reversed(children):
                    try:
                        if child.is_running():
                            child.kill()
                    except psutil.NoSuchProcess:
                        pass
                    except (psutil.Error, OSError) as exc:
                        remaining[child.pid] = child
                        self.log(f"无法结束已登记的子进程 PID {child.pid}：{exc}")
                with self._current_lock:
                    if process.poll() is not None and not remaining:
                        self._owned_roots.pop(process.pid, None)
                        self._owned_children.pop(process.pid, None)
                    else:
                        self._owned_children[process.pid] = remaining
                self._remove_current_process(process)

    def _task_monitor_spec(self, task: TaskConfig) -> Dict[str, Any]:
        """把“目标进程关键词 / 游戏窗口关键词”同步为主等待逻辑。

        优先级：
        1. 游戏窗口关键词：作为主要监控目标，适合鸣潮/原神/Endfield/mumu。
        2. 目标进程关键词：作为主要监控目标，适合 BetterGI.exe/MAA.exe/Endfield.exe。
        3. 旧 wait_mode + wait_process_name：仅作为兼容兜底。
        4. direct_process：只看简小松直接启动的脚本进程。
        """
        window_keywords = normalize_keywords(task.window_keywords)
        process_keywords = normalize_keywords(task.process_keywords)
        legacy_keyword = str(task.wait_process_name or "").strip()

        if window_keywords:
            return {
                "kind": "window",
                "label": "游戏窗口关键词",
                "keywords": window_keywords,
                "source": "window_keywords",
            }
        if process_keywords:
            return {
                "kind": "process",
                "label": "目标进程关键词",
                "keywords": process_keywords,
                "source": "process_keywords",
            }
        if task.wait_mode == "window_title" and legacy_keyword:
            return {
                "kind": "window",
                "label": "窗口标题关键词",
                "keywords": [legacy_keyword],
                "source": "legacy_wait_mode",
            }
        if task.wait_mode == "cmdline_keyword" and legacy_keyword:
            return {
                "kind": "cmdline",
                "label": "命令行关键词",
                "keywords": [legacy_keyword],
                "source": "legacy_wait_mode",
            }
        if task.wait_mode == "process_name" and legacy_keyword:
            return {
                "kind": "process",
                "label": "目标进程关键词",
                "keywords": [legacy_keyword],
                "source": "legacy_wait_mode",
            }
        return {
            "kind": "direct",
            "label": "直接启动进程",
            "keywords": [],
            "source": "direct_process",
        }

    def _monitor_spec_text(self, monitor_spec: Dict[str, Any]) -> str:
        keywords = [str(item).strip() for item in monitor_spec.get("keywords", []) if str(item).strip()]
        if keywords:
            return f"{monitor_spec.get('label', '监控目标')}：{', '.join(keywords)}"
        return str(monitor_spec.get("label", "直接启动进程"))

    def _is_process_keyword_present(self, keyword: str) -> bool:
        """按“关键词”检测目标进程。

        先尝试原有 tasklist 精确镜像名逻辑，再用 psutil 兜底按进程名/命令行包含匹配。
        这样既兼容 BetterGI.exe，也兼容 OK-WW v3.3.8 Global 这类显示名/命令行关键词。
        """
        keyword = str(keyword or "").strip()
        if not keyword:
            return False
        if self._is_process_name_running(keyword):
            return True
        if psutil is None or platform.system().lower() != "windows":
            return False
        lower_keyword = keyword.lower()
        try:
            current_pid = os.getpid()
            for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                try:
                    pid = int(proc.info.get("pid") or 0)
                    if not pid or pid == current_pid:
                        continue
                    proc_name = str(proc.info.get("name") or "")
                    cmdline_items = proc.info.get("cmdline") or []
                    cmdline_text = " ".join(str(item) for item in cmdline_items)
                    if lower_keyword in proc_name.lower() or lower_keyword in cmdline_text.lower():
                        return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
                except Exception:
                    continue
        except Exception:
            return False
        return False

    def _is_monitor_present(self, monitor_spec: Dict[str, Any]) -> tuple[bool, str]:
        kind = str(monitor_spec.get("kind", "direct"))
        keywords = [str(item).strip() for item in monitor_spec.get("keywords", []) if str(item).strip()]
        for keyword in keywords:
            if kind == "window" and self._is_window_title_present(keyword):
                return True, keyword
            if kind == "process" and self._is_process_keyword_present(keyword):
                return True, keyword
            if kind == "cmdline" and self._is_commandline_keyword_running(keyword):
                return True, keyword
        return False, ""

    def _run_one_task(self, task: TaskConfig) -> str:
        name = task.name.strip() or f"任务{task.order}"
        script_text = task.script_path.strip()
        timeout_seconds = max(0, int(task.timeout_minutes)) * 60
        executable = Path(script_text or name).expanduser().resolve()
        try:
            executable, _ = resolve_task_command(task, executable)
        except (ValueError, OSError):
            pass
        self.task_report_signal.emit({"task_id": task.task_id, "task_name": name,
                                      "script_path": str(executable), "result": "running",
                                      "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})

        def record(elapsed, status, result, source=""):
            report = self.stats_manager.add_record(name, elapsed, status, task.wait_mode,
                                                   script_path=str(executable), result=result, source=source,
                                                   task_id=task.task_id)
            self.task_report_signal.emit(report)

        self.log(f"开始执行任务：{name}")
        self.log(f"脚本路径：{script_text}")
        if task.use_args and task.args.strip():
            self.log(f"启动参数：{task.args.strip()}")
        monitor_spec = self._task_monitor_spec(task)
        if task.wait_mode == "fire_and_continue":
            self.log("等待模式：启动后不等待；此任务启动成功后会立即进入后续任务。")
        else:
            self.log(f"监控模式：{self._monitor_spec_text(monitor_spec)}。监控目标出现后开始等待，连续消失 {task.monitor_absent_grace_seconds} 秒后继续下一个任务；超时仅作为兜底。")
        if task.concurrent_group.strip():
            self.log(f"并发组：{task.concurrent_group.strip()}，组策略：{CONCURRENT_POLICIES.get(task.concurrent_policy, task.concurrent_policy)}")

        if not script_text:
            message = f"任务「{name}」脚本路径为空，已跳过。"
            self.log(message)
            self.mark_task_error(message, name)
            record(0, message, "failed")
            return "continue"

        script_path = Path(script_text).expanduser().resolve()
        if not script_path.exists():
            message = f"任务「{name}」脚本不存在，已跳过：{script_path}"
            self.log(message)
            self.mark_task_error(message, name)
            record(0, message, "failed")
            return "continue"

        if not script_path.is_file():
            message = f"任务「{name}」路径不是文件，已跳过：{script_path}"
            self.log(message)
            self.mark_task_error(message, name)
            record(0, message, "failed")
            return "continue"

        try:
            executable, _ = resolve_task_command(task, script_path)
            command = self._build_command(script_path, task)
        except Exception as exc:
            message = f"任务「{name}」构建启动命令失败，已跳过：{exc}"
            self.log(message)
            self.mark_task_error(message, name)
            record(0, message, "failed")
            return "continue"

        cwd = str(script_path.parent)
        log_probe = TaskLogProbe.capture(executable)

        try:
            with self._launch_lock:
                if self.stop_event.is_set():
                    record(0, "已取消启动", "stopped")
                    return "stop_requested"
                process = self._start_process(command, cwd)
                with self._current_lock:
                    self._owned_roots[process.pid] = process
                self._add_current_process(process, task=task)
            self._track_children(process)
        except Exception as exc:
            message = f"任务「{name}」启动失败，已跳过：{exc}"
            self.log(message)
            self.mark_task_error(message, name)
            record(0, message, "failed")
            return "continue"

        self.log(f"任务「{name}」已启动，PID：{process.pid}")
        start_time = self._active_time()
        if not self._confirm_launch_success(task, process, name, start_time, monitor_spec):
            self._remove_current_process(process)
            if self.stop_event.is_set():
                record(int(self._active_time() - start_time), "已停止", "stopped")
                return "stop_requested"
            record(int(self._active_time() - start_time), "启动失败或提前退出", "failed")
            return "continue"

        self.task_launch_success_signal.emit(name)
        if task.wait_mode == "fire_and_continue":
            self.log(f"任务「{name}」已按“启动后不等待”处理，继续后续任务。")
            self._remove_current_process(process)
            self.had_unverified_result = True
            self.mark_task_warning(f"任务「{name}」已启动，未等待执行结果。", name)
            record(0, "已启动，未等待结果", "unknown")
            return "continue"
        if task.confirm_enter_delay_seconds > 0:
            self._schedule_press_enter(task.confirm_enter_delay_seconds, name, process)

        last_emit_second = -1
        # v28.9：把“远早于预估时间就退出”视为问题日志。
        # 这里沿用表格里的“最大运行时间(分钟)”作为预估/保护时间；
        # 实际耗时不足预估的 1/6 时记录快速退出异常，并继续执行队列。
        early_exit_threshold = timeout_seconds / 6 if timeout_seconds > 0 else 0

        monitor_seen = False
        monitor_wait_logged = False
        monitor_absent_since: Optional[float] = None
        monitor_absent_grace_seconds = max(1, min(3600, task.monitor_absent_grace_seconds))
        handoff_deadline = None
        warning_emitted = False

        def finish_with_log(status: str, *, check_early_exit: bool = True,
                            verify_result: bool = True) -> str:
            result = "unknown" if verify_result else "failed"
            source = ""
            if log_probe and verify_result:
                verdict = self._read_task_result(log_probe, process, name)
                if verdict is None:
                    record(int(self._active_time() - start_time), "核对日志时停止", "stopped")
                    return "stop_requested"
                result, source = verdict.status, verdict.source
                label = {"success": "日志确认完成", "failed": "日志确认失败",
                         "unknown": "结果未确认"}[verdict.status]
                suffix = f"（{verdict.source}）" if verdict.source else ""
                message = f"任务「{name}」{label}：{verdict.evidence}{suffix}"
                self.log(message)
                status = f"{label}：{verdict.evidence}{suffix}"
                if verdict.status == "failed":
                    self.mark_task_error(message, name)
                elif verdict.status == "unknown":
                    self.had_unverified_result = True
                    self.mark_task_warning(message, name)
            elapsed_done = int(self._active_time() - start_time)
            self.elapsed_signal.emit(elapsed_done)
            self.log(f"任务「{name}」{status}，耗时：{format_seconds(elapsed_done)}。")
            if task.enable_watchdog and check_early_exit and early_exit_threshold > 0 and elapsed_done < early_exit_threshold:
                message = (
                    f"任务「{name}」疑似过早退出：实际耗时 {format_seconds(elapsed_done)}，"
                    f"低于预估/最大时间 1/6（{format_seconds(int(early_exit_threshold))}）。"
                )
                self.log(message)
                self.mark_task_warning(message, name)
            self._remove_current_process(process)
            record(elapsed_done, status, result, source)
            return "continue"

        while True:
            self._wait_if_paused()
            if self.stop_event.is_set():
                self.log(f"任务「{name}」执行中收到停止请求，正在按统一清理顺序收尾当前任务。")
                self._cleanup_processes_for_tasks([task], [process], scope_label="停止执行", include_window_keywords=True)
                self._remove_current_process(process)
                record(int(self._active_time() - start_time), "已停止", "stopped")
                return "stop_requested"

            self._track_children(process)
            return_code = process.poll()
            now = self._active_time()
            monitor_kind = str(monitor_spec.get("kind", "direct"))

            # 新关键词字段接管主等待逻辑。
            # 优先按“游戏窗口关键词”，其次按“目标进程关键词”，旧 wait_mode 只做兼容兜底。
            if monitor_kind in ("window", "process", "cmdline"):
                present, matched_keyword = self._is_monitor_present(monitor_spec)
                label = str(monitor_spec.get("label", "监控目标"))
                if present:
                    monitor_absent_since = None
                    if not monitor_seen:
                        monitor_seen = True
                        self.log(f"检测到{label}「{matched_keyword}」，现在开始等待它消失。")
                elif monitor_seen:
                    if monitor_absent_since is None:
                        monitor_absent_since = now
                        self.log(f"{label}暂时消失，开始 {int(monitor_absent_grace_seconds)} 秒确认，避免更新/闪退造成误判。")
                    elif now - monitor_absent_since >= monitor_absent_grace_seconds:
                        return finish_with_log(f"{label}已连续消失 {int(monitor_absent_grace_seconds)} 秒")
                elif return_code is not None:
                    if handoff_deadline is None:
                        handoff_deadline = now + 180
                    if not monitor_wait_logged:
                        monitor_wait_logged = True
                        self.log(f"直接启动进程已结束，仍在等待{label}出现：{', '.join(monitor_spec.get('keywords', []))}。")
                    if now >= handoff_deadline:
                        message = f"直接进程已结束，180 秒内未检测到{label}「{', '.join(monitor_spec.get('keywords', []))}」"
                        self.mark_task_error(message, name)
                        return finish_with_log(message, check_early_exit=False, verify_result=False)
            elif return_code is not None:
                if return_code != 0:
                    message = f"任务「{name}」异常退出，退出码：{return_code}"
                    self.mark_task_error(message, name)
                    return finish_with_log(message, check_early_exit=False, verify_result=False)
                return finish_with_log("正常结束，退出码：0")

            elapsed = int(now - start_time)
            if task.enable_watchdog and timeout_seconds and not warning_emitted and elapsed >= timeout_seconds * 5 / 6:
                warning_emitted = True
                message = f"任务「{name}」已运行至时间上限的 5/6。"
                self.log(message)
                self.mark_task_warning(message, name)
            if elapsed != last_emit_second:
                last_emit_second = elapsed
                self.elapsed_signal.emit(elapsed)

            if timeout_seconds > 0 and elapsed >= timeout_seconds:
                self.status_signal.emit("超时处理中")
                timeout_message = (
                    f"任务「{name}」已超时，最大运行时间：{task.timeout_minutes} 分钟，"
                    f"超时处理方式：{task.timeout_action}。"
                )
                if log_probe:
                    verdict = log_probe.inspect(self._task_process_ids(process))
                    timeout_message += f" 脚本日志：{verdict.evidence}"
                self.log(timeout_message)
                screenshot_names: List[str] = []
                # v31.12：超时截图只受全局设置控制，不再叠加任务级开关。
                should_capture = bool(getattr(self, "enable_timeout_screenshot", False))
                if should_capture:
                    before_path = self._capture_timeout_screenshot(name, "before_kill")
                    if before_path:
                        screenshot_names.append(before_path.name)
                result_timeout = self._handle_timeout(task, process, name)
                if should_capture:
                    after_path = self._capture_timeout_screenshot(name, "after_kill")
                    if after_path:
                        screenshot_names.append(after_path.name)
                if screenshot_names:
                    timeout_message = f"{timeout_message} 已保存现场截图：{', '.join(screenshot_names)}"
                self.mark_task_error(timeout_message, name)
                record(elapsed, timeout_message, "failed", verdict.source if log_probe else "")
                return result_timeout

            time.sleep(0.25)

    def _task_process_ids(self, process):
        self._track_children(process)
        with self._current_lock:
            return {process.pid, *self._owned_children.get(process.pid, {})}

    def _read_task_result(self, probe, process, name):
        # 三月七在窗口消失后仍可能写入最终 JSON。仅在原进程仍运行时延长等待。
        self.log(f"任务「{name}」已结束窗口或进程监控，正在核对本轮脚本日志。")
        verdict = probe.inspect(self._task_process_ids(process))
        timeout = 75 if probe.kind == "march7th" and (process.poll() is None or verdict.source) else 3
        deadline = time.monotonic() + timeout
        while not self.stop_event.is_set():
            if verdict.pending and time.monotonic() < deadline:
                self.stop_event.wait(0.75)
                verdict = probe.inspect(self._task_process_ids(process))
                continue
            return verdict
        return None

    def _confirm_launch_success(self, task: TaskConfig, process: Any, name: str, start_time: float, monitor_spec: Dict[str, Any]) -> bool:
        """启动后短暂确认，避免脚本秒退却把 UI 自动切回主界面。

        确认阶段也同步使用新监控目标。
        如果启动脚本很快退出，但游戏窗口/目标进程已经出现，则仍认为启动成功。
        """
        confirm_seconds = 2.0
        deadline = start_time + confirm_seconds
        while self._active_time() < deadline:
            self._wait_if_paused()
            if self.stop_event.is_set():
                return False
            self._track_children(process)
            return_code = process.poll()
            if return_code is not None:
                present, matched_keyword = self._is_monitor_present(monitor_spec)
                if present:
                    label = str(monitor_spec.get("label", "监控目标"))
                    self.log(f"任务「{name}」直接进程已退出，但已检测到{label}「{matched_keyword}」，判定启动成功。")
                    return True
                if return_code == 0 and monitor_spec.get("kind") in {"process", "window", "cmdline"}:
                    self.log(f"任务「{name}」启动器已退出，继续等待配置的监控目标。")
                    return True
                if return_code == 0 and task.wait_mode != "fire_and_continue":
                    return True
                message = f"任务「{name}」启动阶段异常退出，退出码：{return_code}。"
                self.log(message)
                self.mark_task_error(message, name)
                return False
            time.sleep(0.2)
        self.log(f"任务「{name}」启动确认通过：进程稳定运行超过 {int(confirm_seconds)} 秒。")
        return True

    def _capture_timeout_screenshot(self, task_name: str, stage: str = "timeout") -> Optional[Path]:
        """超时处理现场截图。stage=before_kill/after_kill，对应强制结束前后。"""
        try:
            screenshots_dir = self.logger.log_dir / "screenshots"
            screenshots_dir.mkdir(parents=True, exist_ok=True)
            safe_name = re.sub(r'[^0-9A-Za-z_\-\u4e00-\u9fff]+', '_', task_name).strip('_') or "task"
            safe_stage = re.sub(r'[^0-9A-Za-z_\-]+', '_', stage).strip('_') or "timeout"
            path = screenshots_dir / f"{self.logger.session_label}_{now_time_text().replace(':', '')}_{safe_name}_timeout_{safe_stage}.png"
            screen = QGuiApplication.primaryScreen()
            if screen is not None:
                pixmap = screen.grabWindow(0)
                if not pixmap.isNull() and pixmap.save(str(path), "PNG"):
                    self.log(f"超时现场截图已保存：{path}")
                    return path
            try:
                from PIL import ImageGrab

                image = ImageGrab.grab(all_screens=True)
                image.save(str(path), "PNG")
                self.log(f"超时现场截图已保存：{path}")
                return path
            except Exception as grab_exc:
                self.log(f"超时截图失败：{grab_exc}")
                return None
        except Exception as exc:
            self.log(f"超时截图失败：{exc}")
            return None

    def _handle_timeout(self, task: TaskConfig, process: subprocess.Popen[Any], name: str) -> str:
        action = task.timeout_action
        if action not in TIMEOUT_ACTIONS:
            action = "kill_and_continue"

        if action == "kill_and_continue":
            self.log(f"任务「{name}」超时：正在按启动脚本进程 → 目标进程 → 游戏窗口/扩展进程顺序强制收尾，然后继续下一个任务。")
            self._cleanup_processes_for_tasks([task], [process], scope_label="超时处理", include_window_keywords=True)
            self._remove_current_process(process)
            self.status_signal.emit("运行中")
            return "continue"

        if action == "skip_and_continue":
            self.log(
                f"任务「{name}」超时：按照配置不结束当前脚本，直接继续下一个任务。"
                "注意：这可能导致多个脚本同时运行。"
            )
            self._remove_current_process(process)
            self.status_signal.emit("运行中")
            return "continue"

        if action == "stop_all":
            self.log(f"任务「{name}」超时：正在停止全部任务，并按统一清理顺序收尾当前任务。")
            self._cleanup_processes_for_tasks([task], [process], scope_label="超时处理", include_window_keywords=True)
            self._remove_current_process(process)
            return "stop_all"

        self.log(f"任务「{name}」超时处理方式未知，默认按统一清理顺序强制结束并继续。")
        self._cleanup_processes_for_tasks([task], [process], scope_label="超时处理", include_window_keywords=True)
        self._remove_current_process(process)
        return "continue"

    def _build_command(self, script_path: Path, task: TaskConfig) -> List[str]:
        executable, args = resolve_task_command(task, script_path)
        suffix = executable.suffix.lower()
        if suffix in {".bat", ".cmd"} and platform.system().lower() == "windows":
            return ["cmd.exe", "/d", "/c", str(executable), *args]
        if suffix == ".py":
            return [self._python_for_child_script(), str(executable), *args]
        return [str(executable), *args]

    def _is_process_name_running(self, process_name: str) -> bool:
        if platform.system().lower() != "windows":
            return False
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {process_name}", "/NH"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="mbcs",
                errors="ignore",
                shell=False,
                timeout=5,
            )
            output = result.stdout.lower()
            return process_name.lower() in output and "没有运行的任务" not in output and "no tasks" not in output
        except Exception:
            return False

    def _is_window_title_present(self, keyword: str) -> bool:
        if platform.system().lower() != "windows" or not keyword:
            return False
        try:
            user32 = ctypes.windll.user32
            titles: List[str] = []
            EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

            def callback(hwnd, lparam):
                if user32.IsWindowVisible(hwnd):
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buff = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buff, length + 1)
                        titles.append(buff.value)
                return True

            user32.EnumWindows(EnumWindowsProc(callback), 0)
            key = keyword.lower()
            return any(key in title.lower() for title in titles)
        except Exception:
            return False

    def _is_commandline_keyword_running(self, keyword: str) -> bool:
        if platform.system().lower() != "windows" or not keyword:
            return False
        try:
            safe_keyword = keyword.replace("'", "''")
            ps_command = (
                "Get-CimInstance Win32_Process | "
                f"Where-Object {{$_.CommandLine -like '*{safe_keyword}*'}} | "
                "Select-Object -First 1 -ExpandProperty ProcessId"
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_command],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="ignore",
                shell=False,
                timeout=8,
            )
            return bool(result.stdout.strip())
        except Exception:
            return False

    def _schedule_press_enter(self, delay_seconds: int, task_name: str, process) -> None:
        def worker() -> None:
            deadline = self._active_time() + delay_seconds
            while self._active_time() < deadline:
                if self.stop_event.wait(0.2):
                    return
                with self._current_lock:
                    if process not in self.current_processes:
                        return
            try:
                self._track_children(process)
                user32 = ctypes.windll.user32
                user32.GetForegroundWindow.restype = wintypes.HWND
                foreground = user32.GetForegroundWindow()
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(foreground, ctypes.byref(pid))
                with self._current_lock:
                    if self.stop_event.is_set() or self.pause_event.is_set() or process not in self.current_processes:
                        return
                    child = self._owned_children.get(process.pid, {}).get(pid.value)
                    owns_foreground = (pid.value == process.pid and process.poll() is None) or (child and child.is_running())
                    if not owns_foreground or user32.GetForegroundWindow() != foreground:
                        self.log(f"任务「{task_name}」未发送 Enter：当前前台窗口不属于本任务。")
                        return
                    user32.keybd_event(0x0D, 0, 0, 0)
                    user32.keybd_event(0x0D, 0, 2, 0)
                self.log(f"任务「{task_name}」已发送一次 Enter 确认键。")
            except Exception as exc:
                self.log(f"任务「{task_name}」发送 Enter 失败：{exc}")

        threading.Thread(target=worker, daemon=True).start()

    def _python_for_child_script(self) -> str:
        """
        用于执行用户配置的 .py 脚本。

        普通 python main.py 运行时，使用当前解释器。
        PyInstaller 打包后，sys.executable 会变成当前 GUI 程序本身，
        此时优先读取环境变量 AUTO_DAILY_PYTHON，否则尝试使用 PATH 中的 python。
        """
        if getattr(sys, "frozen", False):
            return os.environ.get("AUTO_DAILY_PYTHON", "python")
        return sys.executable

    def _start_process(self, command: List[str], cwd: str) -> Any:
        creationflags = 0
        if platform.system().lower() == "windows":
            creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

        try:
            return native_popen(
                command,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                creationflags=creationflags,
            )
        except OSError as exc:
            # Windows 上带 requireAdministrator 清单的 exe，非管理员 Popen 会报 WinError 740。
            # 这里自动改用 runas 拉起 UAC，而不是让用户再手动右键管理员启动。
            if platform.system().lower() == "windows" and getattr(exc, "winerror", None) == 740:
                self.log("检测到该脚本需要管理员权限，正在尝试弹出 UAC 提权启动。")
                return self._start_process_elevated(command, cwd)
            raise

    def _start_process_elevated(self, command: List[str], cwd: str) -> ElevatedProcessAdapter:
        if platform.system().lower() != "windows":
            raise RuntimeError("当前平台不支持 runas 提权启动。")
        if not command:
            raise RuntimeError("启动命令为空。")

        executable = command[0]
        params = subprocess.list2cmdline(command[1:]) if len(command) > 1 else ""

        class SHELLEXECUTEINFOW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("fMask", wintypes.ULONG),
                ("hwnd", wintypes.HWND),
                ("lpVerb", wintypes.LPCWSTR),
                ("lpFile", wintypes.LPCWSTR),
                ("lpParameters", wintypes.LPCWSTR),
                ("lpDirectory", wintypes.LPCWSTR),
                ("nShow", ctypes.c_int),
                ("hInstApp", wintypes.HINSTANCE),
                ("lpIDList", wintypes.LPVOID),
                ("lpClass", wintypes.LPCWSTR),
                ("hkeyClass", wintypes.HKEY),
                ("dwHotKey", wintypes.DWORD),
                ("hIcon", wintypes.HANDLE),
                ("hProcess", wintypes.HANDLE),
            ]

        SEE_MASK_NOCLOSEPROCESS = 0x00000040
        SW_SHOWNORMAL = 1
        sei = SHELLEXECUTEINFOW()
        sei.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
        sei.fMask = SEE_MASK_NOCLOSEPROCESS
        sei.hwnd = None
        sei.lpVerb = "runas"
        sei.lpFile = str(executable)
        sei.lpParameters = params
        sei.lpDirectory = str(cwd)
        sei.nShow = SW_SHOWNORMAL

        ok = ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei))
        if not ok:
            err = ctypes.get_last_error()
            if err == 1223:
                raise RuntimeError("用户取消了 UAC 管理员权限确认。")
            raise ctypes.WinError(err)

        pid = wintypes.DWORD()
        if not ctypes.windll.kernel32.GetProcessId(wintypes.HANDLE(sei.hProcess)):
            pid_value = 0
        else:
            pid_value = int(ctypes.windll.kernel32.GetProcessId(wintypes.HANDLE(sei.hProcess)))
        return ElevatedProcessAdapter(int(sei.hProcess), pid_value)

    def _terminate_process(self, process: subprocess.Popen[Any]) -> None:
        if process.poll() is not None:
            self.log(f"当前脚本进程已经退出，退出码：{process.returncode}。")
            return

        try:
            self.log(f"正在终止脚本进程 PID：{process.pid}")
            process.terminate()
            try:
                process.wait(timeout=5)
                self.log(f"脚本进程已终止，退出码：{process.returncode}。")
                return
            except subprocess.TimeoutExpired:
                self.log("普通终止超时，正在强制 kill。")
                process.kill()
                process.wait(timeout=5)
                self.log(f"脚本进程已被强制结束，退出码：{process.returncode}。")
        except Exception as exc:
            self.log(f"终止脚本进程失败：{exc}")

# ============================================================
# V31.11 日志 Session 隔离升级
#
# 新日志结构：
#
# ===== SESSION START : task =====
# ...
# ===== SESSION END : task =====
#
# 用于避免：
# - m9a
# - maaend
# - okww
#
# 等多个脚本日志发生串流污染。
# ============================================================
