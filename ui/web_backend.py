# -*- coding: utf-8 -*-
"""PyWebView 前端桥接层：完整复用 core 调度、统计与更新逻辑。"""
from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional
import webbrowser
import winreg

from PySide6.QtCore import Qt

from core import (
    APP_NAME,
    AUTHOR_URL,
    CONCURRENT_POLICIES,
    CONFIG_FILE_NAME,
    GITHUB_URL,
    LOG_DIR_NAME,
    TIMEOUT_ACTIONS,
    WAIT_MODES,
    AppConfig,
    ConfigManager,
    FileLogger,
    RuntimeStatsManager,
    ScriptRunnerWorker,
    TaskConfig,
    app_dir,
    format_seconds,
    normalize_keywords,
)
from core.app_release import RELEASES_URL, check_app_release
from core.core import APP_VERSION, PROJECT_URL, ensure_unique_task_ids
from core.daily_schedule import DailySchedule, ScheduleError
from core.mail_alerts import (
    get_code, qq_address, render_mail, render_run_mail, send_mail, store_code, summarize_outcomes,
)
from core.native_process import native_popen
from core.script_reports import iter_script_reports, load_script_reports, report_payload
from core.task_icons import task_icon
from core.updates import MAA_UPDATE_IN_APP, READY_UPDATE_STATUSES, check_item, discover_items, update_item, UpdateCancelled


SCRIPT_CATALOG_URL = "https://mirrorchyan.com/zh/projects"
SCRIPT_CATALOG_NOTICE_SECONDS = 7


def resolve_asset_file(base_dir: Path, *parts: str) -> Optional[Path]:
    """优先从运行目录查找资源，缺失时回退到 PyInstaller _MEIPASS 资源目录。"""
    candidate = base_dir / "assets" / Path(*parts)
    if candidate.exists():
        return candidate
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundled = Path(meipass) / "assets" / Path(*parts)
        if bundled.exists():
            return bundled
    return None


def resolve_doc_file(base_dir: Path, *parts: str) -> Optional[Path]:
    candidate = base_dir / "docs" / Path(*parts)
    if candidate.exists():
        return candidate
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundled = Path(meipass) / "docs" / Path(*parts)
        if bundled.exists():
            return bundled
    return None


class JianszWebBridge:
    """暴露给 PyWebView 前端调用的线程安全 Python API。"""

    def __init__(self, base_dir: Optional[Path] = None, *, agent_host: bool = False) -> None:
        self._base_dir = base_dir or app_dir()
        self._config_manager = ConfigManager(self._base_dir / CONFIG_FILE_NAME)
        self._file_logger = FileLogger(self._base_dir / LOG_DIR_NAME)
        self._stats_manager = RuntimeStatsManager(self._base_dir)
        self._daily_schedule = DailySchedule(self._base_dir)
        self._config = self._config_manager.load()
        self._agent_host = agent_host
        self._script_report_day = date.today()
        self._script_reports = load_script_reports(self._stats_manager.stats_dir, self._config.tasks,
                                                   day=self._script_report_day)

        self._lock = threading.RLock()
        self._events: deque[Dict[str, Any]] = deque(maxlen=2000)
        self._logs_history: deque[str] = deque(maxlen=600)
        self._window: Any = None
        self._hide_in_tray: Optional[Callable[[], None]] = None
        self._script_catalog_notice_ready_at: Optional[float] = None
        self._maximized = False

        self._worker: Optional[ScriptRunnerWorker] = None
        self._worker_thread: Optional[threading.Thread] = None
        self._is_running = False
        self._is_paused = False
        self._status_text = "空闲待命"
        self._current_task_name = "暂无运行任务"
        self._progress = {"current": 0, "total": 0}
        self._elapsed = 0
        self._run_task_error_count = 0
        self._reported_abnormal_messages: set[str] = set()

        self._update_items: list[Any] = []
        self._update_stop = threading.Event()
        self._update_busy = False
        self._prepared_update_ticket = None
        self._mail_settings = self._config.extra_settings.get("qq_mail_alerts", {})
        if not isinstance(self._mail_settings, dict):
            self._mail_settings = {}
        self._run_mail_context = None
        self._run_mail_reports = {}
        self._deferred_shutdown = None
        self._shutdown_pending = False
        self._shutdown_deadline = 0.0
        self._close_requested = False
        self._allow_close = False

        self._append_log("程序启动（现代 WebView 引擎已就绪）。")
        self._append_log(f"配置文件：{self._config_manager.config_path}")
        self._append_log(f"日志目录：{self._file_logger.log_dir}")

    # -------------------- 内部辅助 --------------------

    def _attach_window(self, window: Any) -> None:
        self._window = window

    def _push_event(self, event_type: str, payload: Any = None) -> None:
        with self._lock:
            self._events.append({"type": event_type, "payload": payload})

    def _append_log(self, message: str, *, already_formatted: bool = False) -> None:
        line = message if already_formatted else self._file_logger.log(message)
        if already_formatted:
            self._file_logger.write_line(line)
        with self._lock:
            self._logs_history.append(line)
        self._push_event("log", {"line": line})

    def _current_startup_command(self) -> str:
        if getattr(sys, "frozen", False):
            return f'"{sys.executable}"'
        return '"{}" "{}"'.format(sys.executable, self._base_dir / "main.py")

    def _remove_legacy_windows_startup(self) -> None:
        """清理指向这份安装的旧版 Run 项，避免留下不同版本的重复入口。"""
        commands = {self._current_startup_command().casefold(),
                    f'"{self._base_dir / "Jiansz.exe"}"'.casefold()}
        try:
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as key:
                entries = [winreg.EnumValue(key, index) for index in range(winreg.QueryInfoKey(key)[1])]
                for name, value, value_type in entries:
                    if (name.startswith("简小松·脚本调度助手 v") and value_type == winreg.REG_SZ
                            and value.casefold() in commands):
                        winreg.DeleteValue(key, name)
        except FileNotFoundError:
            pass
        except OSError as exc:
            self._append_log(f"清理旧版 Windows 自启动记录失败：{exc}")

    def _migrate_windows_startup(self) -> None:
        with self._lock:
            if self._config.windows_startup:
                try:
                    self._daily_schedule.save_startup(True)
                except ScheduleError as exc:
                    self._append_log(f"迁移 Windows 开机自启动失败：{exc}")
                    self._push_event("toast", {"tone": "error", "message": str(exc)})
                    return
            self._remove_legacy_windows_startup()

    def _record_abnormal_report(self, message: str, task_name: str = "") -> None:
        clean_msg = str(message or "").strip()
        if not clean_msg:
            return
        key = f"{task_name}|{clean_msg}"
        if key in self._reported_abnormal_messages:
            return
        self._reported_abnormal_messages.add(key)
        report_path = self._base_dir / "logs" / "last_abnormal_report.json"
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            data: Dict[str, Any] = {"events": []}
            if report_path.exists():
                with report_path.open("r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict) and isinstance(loaded.get("events"), list):
                    data = loaded
            data["events"].append({
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "task": task_name,
                "message": clean_msg,
            })
            with report_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _read_abnormal_report(self) -> Optional[Dict[str, Any]]:
        report_path = self._base_dir / "logs" / "last_abnormal_report.json"
        if not report_path.exists():
            return None
        try:
            with report_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            events = data.get("events", []) if isinstance(data, dict) else []
            if not events:
                report_path.unlink(missing_ok=True)
                return None
            return {"events": events}
        except Exception:
            return None

    def _serialize_config(self) -> Dict[str, Any]:
        return {
            "theme": self._config.theme,
            "shutdown_after_done": bool(self._config.shutdown_after_done),
            "shutdown_delay_seconds": int(self._config.shutdown_delay_seconds),
            "auto_exit_after_done": bool(self._config.auto_exit_after_done),
            "close_to_tray": bool(self._config.close_to_tray),
            "auto_start_tasks": bool(self._config.auto_start_tasks),
            "windows_startup": bool(self._config.windows_startup),
            "enable_timeout_screenshot": bool(self._config.enable_timeout_screenshot),
            "auto_update_scripts_before_launch": True,
            "tasks": [t.to_dict() for t in self._config.tasks],
        }

    def _read_stats_payload(self) -> Dict[str, Any]:
        summary_path = self._base_dir / "runtime_stats" / "runtime_summary.json"
        if not summary_path.exists():
            return {"rows": [], "totalRuns": 0, "scriptCount": 0, "lastTime": "—"}
        try:
            with summary_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or not data:
                return {"rows": [], "totalRuns": 0, "scriptCount": 0, "lastTime": "—"}
            rows = []
            for name, item in data.items():
                if not isinstance(item, dict):
                    continue
                avg_sec = int(float(item.get("average_seconds", 0)))
                last_sec = int(item.get("last_seconds", 0))
                rows.append({
                    "name": str(name),
                    "count": int(item.get("count", 0)),
                    "averageSeconds": avg_sec,
                    "averageText": format_seconds(avg_sec),
                    "lastSeconds": last_sec,
                    "lastText": format_seconds(last_sec),
                    "lastTime": str(item.get("last_time", "")),
                    "lastStatus": str(item.get("last_status", "")),
                })
            rows.sort(key=lambda x: x["name"].lower())
            total_runs = sum(r["count"] for r in rows)
            last_time = max((r["lastTime"] for r in rows if r["lastTime"]), default="—")
            return {
                "rows": rows,
                "totalRuns": total_runs,
                "scriptCount": len(rows),
                "lastTime": last_time,
            }
        except Exception as exc:
            return {"rows": [], "totalRuns": 0, "scriptCount": 0, "lastTime": "—", "error": str(exc)}

    def _running_snapshot(self) -> Dict[str, Any]:
        return {
            "isRunning": self._is_running,
            "isPaused": self._is_paused,
            "statusText": self._status_text,
            "currentTask": self._current_task_name,
            "progress": dict(self._progress),
            "elapsed": self._elapsed,
            "elapsedText": format_seconds(self._elapsed),
        }

    # -------------------- PyWebView 窗口控制 --------------------

    def win_minimize(self) -> None:
        if self._window is not None:
            self._window.minimize()

    def win_toggle_maximize(self) -> bool:
        if self._window is not None:
            if self._maximized:
                self._window.restore()
                self._maximized = False
            else:
                self._window.maximize()
                self._maximized = True
        return self._maximized

    def win_request_close(self) -> None:
        if not self._config.close_to_tray:
            self.win_close()
        elif self._hide_in_tray is not None:
            self._hide_in_tray()
        else:
            self._push_event("toast", {"tone": "error", "message": "系统托盘尚未就绪，请稍后重试。"})

    def win_close(self) -> None:
        with self._lock:
            if self._update_busy or self._is_running:
                if self._close_requested:
                    return
                self._close_requested = True
                self.updates_stop()
                worker = self._worker
                if worker:
                    worker.request_stop(cleanup=False)
                return
            self._allow_close = True
        if self._window is not None:
            self._window.destroy()

    def _on_window_closing(self):
        if self._allow_close or not (self._update_busy or self._is_running):
            return True
        self.win_close()
        return False

    # -------------------- 启动初始化与轮询 --------------------

    def _refresh_script_report_day(self) -> None:
        """由轮询触发换日，保留正在进行的跨午夜任务。调用方持有锁。"""
        today = date.today()
        if today == self._script_report_day:
            return
        reports = load_script_reports(self._stats_manager.stats_dir, self._config.tasks, day=today)
        reports.update((key, report) for key, report in self._script_reports.items()
                       if report["result"] == "running" or report["time"][:10] == today.isoformat())
        self._script_report_day = today
        self._script_reports = reports
        self._push_event("script_reports_reset", dict(reports))

    def get_bootstrap(self) -> Dict[str, Any]:
        with self._lock:
            self._refresh_script_report_day()
            logs = list(self._logs_history)
            reports = {key: dict(value) for key, value in self._script_reports.items()}
            self._events = deque((e for e in self._events if e.get("type") != "log"), maxlen=2000)
        return {
            "appName": APP_NAME,
            "appVersion": APP_VERSION,
            "config": self._serialize_config(),
            "stats": self._read_stats_payload(),
            "logs": logs,
            "scriptReports": reports,
            "autoStartOnOpen": self._config.auto_start_tasks and not self._agent_host,
            "runner": self._running_snapshot(),
            "abnormalReport": self._read_abnormal_report(),
            "options": {
                "waitModes": WAIT_MODES,
                "timeoutActions": TIMEOUT_ACTIONS,
                "concurrentPolicies": CONCURRENT_POLICIES,
            },
            "links": {
                "author": AUTHOR_URL,
                "github": GITHUB_URL,
            },
        }

    def drain_events(self) -> List[Dict[str, Any]]:
        with self._lock:
            self._refresh_script_report_day()
            items = list(self._events)
            self._events.clear()
        return items

    def dismiss_abnormal_report(self) -> bool:
        report_path = self._base_dir / "logs" / "last_abnormal_report.json"
        try:
            report_path.unlink(missing_ok=True)
            return True
        except Exception:
            return False

    # -------------------- 配置保存与导入导出 --------------------

    def get_mail_alert_settings(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "enabled": bool(self._mail_settings.get("enabled")),
                "sender": self._mail_settings.get("sender", ""),
                "recipient": self._mail_settings.get("recipient", ""),
            }

    def save_mail_alert_settings(self, enabled: bool, sender: str, recipient: str, code: str) -> Dict[str, Any]:
        sender, recipient = sender.strip(), recipient.strip()
        if not qq_address(sender) or not qq_address(recipient):
            return {"ok": False, "error": "请填入有效的 QQ 发件和收件地址。"}
        settings = {"enabled": bool(enabled), "sender": sender, "recipient": recipient}
        with self._lock:
            try:
                if code.strip():
                    store_code(self._base_dir, sender, "".join(code.split()))
                if enabled and not get_code(self._base_dir, sender):
                    return {"ok": False, "error": "请填写 QQ 邮箱 SMTP 授权码。"}
            except Exception as exc:
                error = getattr(exc, "winerror", None) or type(exc).__name__
                self._append_log(f"读取或保存 QQ 邮件授权码失败（{error}）。")
                return {"ok": False, "error": f"无法读取或保存授权码（{error}）。"}
            old = self._config.extra_settings
            self._config.extra_settings = {**old, "qq_mail_alerts": settings}
            try:
                self._config_manager.save(self._config)
            except Exception as exc:
                self._config.extra_settings = old
                error = getattr(exc, "winerror", None) or type(exc).__name__
                self._append_log(f"写入 QQ 邮件设置失败（{error}）。")
                return {"ok": False, "error": f"无法写入邮件配置（{error}）。"}
            self._mail_settings = settings
        return {"ok": True}

    def test_mail_alert(self) -> Dict[str, Any]:
        settings = self.get_mail_alert_settings()
        body = "这是一封简小松测试邮件。收到邮件后，即可确认异常通知渠道可用。"
        sent = self._send_mail_alert(
            settings, "简小松 · 邮件通知测试", body,
            html_body=render_mail("邮件通知测试", body),
        )
        return {"ok": sent, "error": "" if sent else "测试邮件未送达 SMTP 服务器，请检查授权码和网络。"}

    def _send_mail_alert(self, settings, subject, body, *, html_body=None) -> bool:
        sender, recipient = settings.get("sender", ""), settings.get("recipient", "")
        try:
            if not qq_address(sender) or not qq_address(recipient):
                raise ValueError("邮箱地址无效")
            code = get_code(self._base_dir, sender)
            if not code:
                raise ValueError("未设置授权码")
            send_mail(sender, recipient, code, subject, body, html_body=html_body)
        except Exception as exc:
            self._append_log(f"QQ 邮件投递失败（{type(exc).__name__}）。")
            return False
        self._append_log("QQ 邮件已提交至发件服务器。")
        return True

    def _notify_by_mail(self, settings, subject, body, *, html_body=None) -> None:
        if not settings.get("enabled") or not body:
            return
        done = threading.Event()

        def deliver():
            try:
                self._send_mail_alert(settings, subject, body, html_body=html_body)
            finally:
                done.set()

        threading.Thread(target=deliver, daemon=True).start()
        if not done.wait(10):
            self._append_log("QQ 邮件投递尚未确认，继续收尾。")

    def save_config(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            if self._is_running or self._update_busy:
                return {"ok": False, "error": "请先结束任务或更新，再修改配置。"}
            return self._save_config(payload)

    def _save_config(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            raw_tasks = payload.get("tasks", [])
            tasks: List[TaskConfig] = []
            if isinstance(raw_tasks, list):
                for idx, item in enumerate(raw_tasks, start=1):
                    if isinstance(item, dict):
                        t = TaskConfig.from_dict(item, fallback_order=idx)
                        t.order = idx
                        t.process_keywords = normalize_keywords(item.get("process_keywords", []))
                        t.window_keywords = normalize_keywords(item.get("window_keywords", []))
                        tasks.append(t)
            ensure_unique_task_ids(tasks)

            theme = str(payload.get("theme", self._config.theme)).lower()
            if theme not in {"light", "dark", "jiansz", "campus", "fresh", "fantasy"}:
                theme = "jiansz"

            extra = dict(self._config.extra_settings)
            if "auto_update_scripts_before_launch" in payload:
                extra["auto_update_scripts_before_launch"] = bool(payload["auto_update_scripts_before_launch"])

            config = AppConfig(
                theme=theme,
                shutdown_after_done=bool(payload.get("shutdown_after_done", False)),
                shutdown_delay_seconds=max(0, int(payload.get("shutdown_delay_seconds", 60))),
                auto_exit_after_done=bool(payload.get("auto_exit_after_done", False)),
                close_to_tray=bool(payload.get("close_to_tray", self._config.close_to_tray)),
                auto_start_tasks=bool(payload.get("auto_start_tasks", False)),
                windows_startup=bool(payload.get("windows_startup", False)),
                enable_timeout_screenshot=bool(payload.get("enable_timeout_screenshot", False)),
                tasks=tasks,
                extra_settings=extra,
            )
            previous_startup = self._config.windows_startup
            startup_changed = config.windows_startup != previous_startup
            if startup_changed:
                self._daily_schedule.save_startup(config.windows_startup)
            try:
                self._config_manager.save(config)
            except Exception:
                if startup_changed:
                    self._daily_schedule.save_startup(previous_startup)
                raise
            self._config = config
            if startup_changed:
                self._remove_legacy_windows_startup()
            self._append_log("配置已保存。")
            return {"ok": True, "config": self._serialize_config()}
        except Exception as exc:
            self._append_log(f"保存配置失败：{exc}")
            return {"ok": False, "error": str(exc)}

    def get_task_icons(self, requests: List[Dict[str, str]]) -> List[str]:
        return [task_icon(str(item.get("path", "")), str(item.get("args", ""))) for item in requests]

    def browse_script_file(self) -> Dict[str, Any]:
        if self._window is None:
            return {"ok": False, "path": ""}
        try:
            import webview

            selected = self._window.create_file_dialog(
                webview.FileDialog.OPEN,
                directory=str(self._base_dir),
                allow_multiple=False,
                file_types=(
                    "脚本与程序 (*.exe;*.py;*.bat;*.cmd;*.ps1)",
                    "所有文件 (*.*)",
                ),
            )
            if selected and len(selected) > 0:
                return {"ok": True, "path": str(selected[0])}
            return {"ok": False, "path": ""}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "path": ""}

    def export_config(self) -> Dict[str, Any]:
        if self._window is None:
            return {"ok": False}
        try:
            import webview

            selected = self._window.create_file_dialog(
                webview.FileDialog.SAVE,
                directory=str(self._base_dir),
                save_filename="auto_script_config_share.json",
                file_types=("JSON 配置文件 (*.json)", "所有文件 (*.*)"),
            )
            if not selected:
                return {"ok": False, "cancelled": True}
            path = Path(selected if isinstance(selected, str) else selected[0])
            share_cfg = AppConfig(
                theme=self._config.theme,
                shutdown_after_done=self._config.shutdown_after_done,
                shutdown_delay_seconds=self._config.shutdown_delay_seconds,
                auto_exit_after_done=self._config.auto_exit_after_done,
                close_to_tray=self._config.close_to_tray,
                auto_start_tasks=self._config.auto_start_tasks,
                windows_startup=False,
                tasks=list(self._config.tasks),
            )
            with path.open("w", encoding="utf-8") as f:
                json.dump(share_cfg.to_dict(), f, ensure_ascii=False, indent=2)
            self._append_log(f"配置已导出：{path}")
            return {"ok": True, "path": str(path)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def import_config(self) -> Dict[str, Any]:
        if self._window is None:
            return {"ok": False}
        try:
            import webview

            selected = self._window.create_file_dialog(
                webview.FileDialog.OPEN,
                directory=str(self._base_dir),
                allow_multiple=False,
                file_types=("JSON 配置文件 (*.json)", "所有文件 (*.*)"),
            )
            if not selected:
                return {"ok": False, "cancelled": True}
            path = Path(selected[0])
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("配置文件顶层结构不是 JSON 对象")
            imported = AppConfig.from_dict(data)
            with self._lock:
                if self._is_running or self._update_busy:
                    return {"ok": False, "error": "请先结束任务或更新，再导入配置。"}
                config = AppConfig.from_dict({**self._config.to_dict(),
                                              "tasks": [task.to_dict() for task in imported.tasks]})
                self._config_manager.save(config)
                self._config = config
                self._script_report_day = date.today()
                self._script_reports = load_script_reports(self._stats_manager.stats_dir, config.tasks,
                                                           day=self._script_report_day)
                reports = dict(self._script_reports)
            self._append_log(f"配置已导入并保存：{path}")
            return {"ok": True, "path": str(path), "config": self._serialize_config(), "scriptReports": reports}
        except Exception as exc:
            self._append_log(f"导入配置失败：{exc}")
            return {"ok": False, "error": str(exc)}

    # -------------------- 任务调度引擎 --------------------

    def get_daily_schedule(self) -> Dict[str, Any]:
        try:
            return {"ok": True, **self._daily_schedule.get()}
        except ScheduleError as exc:
            return {"ok": False, "error": str(exc)}

    def save_daily_schedule(self, enabled: bool, clock: str) -> Dict[str, Any]:
        with self._lock:
            try:
                schedule = self._daily_schedule.save(bool(enabled), str(clock))
            except ScheduleError as exc:
                return {"ok": False, "error": str(exc)}
        self._append_log(f"每日定时启动已设为 {schedule['time']}。" if schedule["enabled"] else "每日定时启动已关闭。")
        return {"ok": True, **schedule}

    def _agent_command(self, action: str) -> Dict[str, Any]:
        with self._lock:
            if self._close_requested or self._allow_close or self._shutdown_pending:
                return {"status": "blocked", "message": "简小松正在退出或等待关机。"}
            if self._is_running:
                if action == "status":
                    return {"status": "running", "message": self._current_task_name}
                return {"status": "running", "message": "已有一轮任务在执行。"}
            if self._update_busy:
                return {"status": "updating", "message": "已有更新操作在进行。"}
            if action == "status":
                return {"status": "idle", "message": self._status_text}
            if action != "start":
                return {"status": "invalid", "message": "未知命令。"}
            if not any(task.enabled for task in self._config.tasks):
                return {"status": "no_tasks", "message": "没有已启用的任务。"}
            result = self._start_updates("prepare", agent_launch=True)
            if not result["ok"]:
                return {"status": "blocked", "message": result["error"]}
            return {"status": "accepted", "message": "已受理，脚本检查和更新通过后将自动执行。"}

    def start_tasks(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        with self._lock:
            if self._update_busy:
                return {"ok": False, "error": "脚本更新尚未结束，请等待更新完成。"}
            if self._close_requested or self._shutdown_pending:
                return {"ok": False, "error": "请先处理退出或自动关机。"}
            if self._is_running:
                return {"ok": False, "error": "任务已经在运行中。"}
            if isinstance(payload, dict) and "tasks" in payload:
                res = self.save_config(payload)
                if not res.get("ok"):
                    return res

            tasks = sorted(self._config.tasks, key=lambda t: t.order)
            enabled_tasks = [t for t in tasks if t.enabled]
            if not enabled_tasks:
                self._append_log("【提示】当前没有启用的任务，请在【任务调度】页面开启至少一个任务开关。")
                return {"ok": False, "error": "当前没有启用的任务，请先在「任务调度」开启至少一个任务。"}

            signature = self._launch_signature()
            ticket = self._prepared_update_ticket
            self._prepared_update_ticket = None
            verified = ticket and ticket[0] == signature and time.monotonic() - ticket[1] < 120
            if not verified:
                return {"ok": False, "needPrepareUpdate": True}

            for task in enabled_tasks:
                if not task.script_path.strip() and task.name.strip():
                    task.script_path = task.name.strip()
                    self._append_log(f"【提示】任务「{task.name}」未指定完整路径，将尝试直接作为程序名启动。")

            self._is_running = True
            self._run_mail_context = (
                dict(self._mail_settings), self._stats_manager.session_id,
                [(task.task_id, task.name) for task in enabled_tasks],
            )
            self._run_mail_reports = {}
            self._deferred_shutdown = None
        self._is_paused = False
        self._run_task_error_count = 0
        self._status_text = "正在执行"
        self._current_task_name = f"准备执行第 1/{len(enabled_tasks)} 个任务"
        self._progress = {"current": 0, "total": len(enabled_tasks)}
        self._elapsed = 0
        self._push_event("runner", self._running_snapshot())
        self._append_log(f"========== 调度流程启动，共 {len(enabled_tasks)} 个已启用任务 ==========")

        worker = ScriptRunnerWorker(
            tasks=tasks,
            shutdown_after_done=self._config.shutdown_after_done,
            shutdown_delay_seconds=self._config.shutdown_delay_seconds,
            logger=self._file_logger,
            stats_manager=self._stats_manager,
            enable_timeout_screenshot=self._config.enable_timeout_screenshot,
        )
        with self._lock:
            self._worker = worker
            if self._close_requested:
                worker.request_stop(cleanup=False)

        direct = Qt.ConnectionType.DirectConnection
        worker.log_signal.connect(self._on_worker_log, direct)
        worker.status_signal.connect(self._on_worker_status, direct)
        worker.progress_signal.connect(self._on_worker_progress, direct)
        worker.task_started_signal.connect(self._on_worker_task_started, direct)
        worker.elapsed_signal.connect(self._on_worker_elapsed, direct)
        worker.shutdown_prompt_signal.connect(self._on_worker_shutdown_prompt, direct)
        worker.task_error_signal.connect(self._on_worker_task_error, direct)
        worker.task_report_signal.connect(self._on_script_report, direct)
        worker.finished_signal.connect(self._on_worker_finished, direct)

        t = threading.Thread(target=worker.run, daemon=True)
        self._worker_thread = t
        t.start()
        self._append_log("后台执行引擎已就绪，正在顺序启动任务...")
        return {"ok": True}

    def pause_tasks(self) -> None:
        if self._worker is not None and self._is_running and not self._is_paused:
            self._is_paused = True
            self._worker.request_pause()
            self._push_event("runner", self._running_snapshot())

    def resume_tasks(self) -> None:
        if self._worker is not None and self._is_running and self._is_paused:
            self._is_paused = False
            self._worker.request_resume()
            self._push_event("runner", self._running_snapshot())

    def stop_tasks(self) -> None:
        worker = self._worker
        if worker is not None and self._is_running:
            self._status_text = "正在停止..."
            self._push_event("runner", self._running_snapshot())
            worker.request_stop()

    def emergency_stop_tasks(self) -> None:
        worker = self._worker
        if worker is not None and self._is_running:
            self._status_text = "正在紧急停止..."
            self._push_event("runner", self._running_snapshot())
            worker.request_emergency_stop()
        else:
            self._append_log("当前没有运行中的调度任务。")

    def cancel_shutdown(self) -> Dict[str, Any]:
        try:
            with self._lock:
                if not self._shutdown_pending:
                    return {"ok": False, "error": "当前没有由简小松安排的关机。"}
                command = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "shutdown.exe"
                result = subprocess.run([str(command), "/a"], capture_output=True, timeout=10,
                                        creationflags=subprocess.CREATE_NO_WINDOW)
                if result.returncode:
                    return {"ok": False, "error": f"取消关机失败（退出码 {result.returncode}）。"}
                self._shutdown_pending = False
            self._append_log("已取消系统自动关机。")
            return {"ok": True, "message": "已取消系统自动关机。"}
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "error": str(exc)}

    def _on_worker_log(self, line: str) -> None:
        with self._lock:
            self._logs_history.append(line)
        self._push_event("log", {"line": line})

    def _on_worker_status(self, status: str) -> None:
        self._status_text = str(status or "").strip() or "空闲待命"
        if self._status_text == "已暂停":
            self._is_paused = True
        elif "运行" in self._status_text:
            self._is_paused = False
        self._push_event("runner", self._running_snapshot())

    def _on_worker_progress(self, current: int, total: int) -> None:
        self._progress = {"current": current, "total": total}
        self._push_event("runner", self._running_snapshot())

    def _on_worker_task_started(self, task_name: str, index: int, total: int) -> None:
        self._current_task_name = f"正在执行 ({index}/{total})：{task_name}"
        self._progress = {"current": index, "total": total}
        self._push_event("runner", self._running_snapshot())

    def _on_script_report(self, record) -> None:
        key = record.get("task_id")
        if key:
            report = report_payload(record)
            with self._lock:
                self._refresh_script_report_day()
                if not report["time"]:
                    report["time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if report["result"] == "running" or report["time"][:10] == self._script_report_day.isoformat():
                    self._script_reports[key] = report
                    self._push_event("script_report", {"key": key, "report": report})
                if report["result"] != "running" and self._run_mail_context:
                    self._run_mail_reports[key] = report

    def _on_worker_elapsed(self, elapsed: int) -> None:
        self._elapsed = int(elapsed)
        self._push_event("runner", self._running_snapshot())

    def _on_worker_task_error(self, message: str) -> None:
        self._run_task_error_count += 1
        self._record_abnormal_report(message)
        self._push_event("toast", {"tone": "error", "message": message})

    def _on_worker_shutdown_prompt(self, delay_seconds: int) -> None:
        with self._lock:
            if self._run_mail_context and self._run_mail_context[0].get("enabled"):
                self._deferred_shutdown = delay_seconds
                return
        self._schedule_shutdown(delay_seconds)

    def _schedule_shutdown(self, delay_seconds: int) -> None:
        delay = max(1, int(delay_seconds))
        with self._lock:
            if self._close_requested or (self._worker and self._worker.stop_event.is_set()):
                return
            command = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "shutdown.exe"
            try:
                result = subprocess.run([str(command), "/s", "/t", str(delay)], capture_output=True,
                                        timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
                if result.returncode:
                    raise OSError(f"Windows 返回退出码 {result.returncode}")
            except (OSError, subprocess.TimeoutExpired) as exc:
                message = f"安排自动关机失败：{exc}"
                self._append_log(message)
                self._push_event("toast", {"tone": "error", "message": message})
                return
            self._shutdown_pending = True
            self._shutdown_deadline = time.time() + delay
        self._push_event("shutdown_warning", {"seconds": delay, "deadline": self._shutdown_deadline})

    def _on_worker_finished(self, stopped_or_error: bool, user_stopped: bool) -> None:
        with self._lock:
            unverified_result = self._worker and self._worker.had_unverified_result
            self._is_paused = False
            if stopped_or_error:
                if user_stopped or "停止" in self._status_text:
                    self._status_text = "已停止"
                else:
                    self._status_text = "已完成（有异常）"
                    count = max(1, self._run_task_error_count)
                    self._record_abnormal_report(f"任务结束，发生了 {count} 次任务异常。")
            else:
                if unverified_result:
                    self._status_text = "已结束（结果待确认）"
                elif "提醒" in self._status_text:
                    self._status_text = "已完成（有提醒）"
                else:
                    self._status_text = "已全部完成"
            mail_context = self._run_mail_context
            self._run_mail_context = None
            outcomes = self._run_mail_reports
            self._run_mail_reports = {}
            deferred_shutdown = self._deferred_shutdown
            self._deferred_shutdown = None
            finish_status = self._status_text
            has_error = self._run_task_error_count > 0
        if mail_context:
            settings, session_id, tasks = mail_context
            alert_tasks = [
                (key, name) for key, name in tasks
                if not (outcomes.get(key, {}).get("result") == "unknown"
                        and outcomes[key].get("status", "").startswith("每日委托"))
            ]
            if user_stopped:
                alert_tasks = [(key, name) for key, name in alert_tasks
                               if outcomes.get(key, {}).get("result") == "failed"]
            body = summarize_outcomes(alert_tasks, outcomes)
            if not body and stopped_or_error and (not user_stopped or has_error):
                body = "手动停止前发生了任务异常。" if user_stopped else finish_status
            if body:
                self._notify_by_mail(
                    settings, "简小松 · 本轮运行有异常",
                    f"运行编号：{session_id}\n调度结果：{finish_status}\n\n{body}",
                    html_body=render_run_mail(alert_tasks, outcomes, finish_status, session_id),
                )
        if deferred_shutdown is not None:
            self._schedule_shutdown(deferred_shutdown)
        with self._lock:
            self._worker = None
            self._worker_thread = None
            self._refresh_script_report_day()
            for key, report in self._script_reports.items():
                if report["result"] == "running":
                    result, status = ("stopped", "已停止") if user_stopped else ("unknown", "本轮未留下最终结果。")
                    report = {**report, "result": result, "status": status,
                              "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
                    self._script_reports[key] = report
                    self._push_event("script_report", {"key": key, "report": report})
            self._is_running = False
            self._push_event("runner", self._running_snapshot())
        self._push_event("stats", self._read_stats_payload())

        if self._close_requested:
            self.win_close()
        elif self._config.auto_exit_after_done and not stopped_or_error and not unverified_result and not self._shutdown_pending:
            self._append_log("任务已完成，正在退出程序。")
            self.win_close()

    # -------------------- 脚本更新器 --------------------

    def _launch_signature(self) -> str:
        return json.dumps([t.to_dict() for t in sorted(self._config.tasks, key=lambda t: t.order) if t.enabled],
                          sort_keys=True, ensure_ascii=True)

    def updates_discover(self, only_enabled: bool = False) -> Dict[str, Any]:
        with self._lock:
            if not self._update_busy:
                self._update_items = discover_items(self._config.tasks)
            return {"items": [self._serialize_update_item(i, item) for i, item in enumerate(self._update_items)],
                    "busy": self._update_busy}

    @staticmethod
    def _serialize_update_item(index: int, item: Any) -> Dict[str, Any]:
        return {
            "index": index,
            "key": getattr(item, "key", ""),
            "name": getattr(item, "name", ""),
            "kind": getattr(item, "kind", ""),
            "current": getattr(item, "current", "") or "—",
            "latest": getattr(item, "latest", "") or "—",
            "status": getattr(item, "status", ""),
            "detail": getattr(item, "detail", "") or str(getattr(item, "executable", "")),
            "executable": str(getattr(item, "executable", "")),
            "progress": getattr(item, "progress", None),
        }

    def updates_start(self, mode: str, selected_indices: Optional[List[int]] = None) -> Dict[str, Any]:
        return self._start_updates(mode, selected_indices)

    def _start_updates(self, mode: str, selected_indices: Optional[List[int]] = None,
                       *, agent_launch: bool = False) -> Dict[str, Any]:
        if mode not in {"check", "prepare", "update"}:
            return {"ok": False, "error": "未知的更新操作。"}
        with self._lock:
            if self._update_busy or self._is_running or self._close_requested or self._shutdown_pending:
                return {"ok": False, "error": "请等待当前任务、更新或关机操作结束。"}
            signature = self._launch_signature()
            if mode == "prepare":
                self._update_items = discover_items(self._config.tasks)
                indices = list(range(len(self._update_items)))
            else:
                indices = (list(range(len(self._update_items))) if selected_indices is None else
                           list(dict.fromkeys(i for i in selected_indices
                                              if isinstance(i, int) and 0 <= i < len(self._update_items))))
                if not indices:
                    return {"ok": False, "error": "请至少勾选一个脚本。"}
            selected = [(idx, self._update_items[idx]) for idx in indices]
            self._update_stop.clear()
            self._update_busy = True
            self._prepared_update_ticket = None
            mail_settings = dict(self._mail_settings)
        self._push_event("update_busy", {
            "busy": True, "mode": mode,
            "agentLaunch": agent_launch,
            "items": [self._serialize_update_item(i, item) for i, item in enumerate(self._update_items)],
        })

        def work() -> None:
            ready = False
            try:
                for row_idx, item in selected:
                    if item.kind != "unsupported":
                        item.status = "正在检查"
                    item.progress = None
                    self._push_event("update_item", self._serialize_update_item(row_idx, item))
                if mode in {"check", "prepare", "update"}:
                    with ThreadPoolExecutor(max_workers=4) as pool:
                        jobs = {
                            pool.submit(
                                check_item,
                                item,
                                self._update_stop,
                                lambda m: self._emit_update_log(m),
                            ): row
                            for row, item in selected
                        }
                        for job in as_completed(jobs):
                            row_idx = jobs[job]
                            updated_item = job.result()
                            self._update_items[row_idx] = updated_item
                            self._push_event("update_item", self._serialize_update_item(row_idx, updated_item))
                if mode in {"prepare", "update"}:
                    for row_idx, item in selected:
                        if self._update_stop.is_set():
                            break
                        if getattr(item, "kind", "") == "unsupported":
                            continue
                        try:
                            if item.status in {"有更新", "更新失败"}:
                                item.status = "正在更新"
                                self._push_event("update_item", self._serialize_update_item(row_idx, item))
                                item.status = "有更新"
                                def progress(event, index=row_idx, current=item):
                                    current.progress = event
                                    self._push_event("update_progress", {"index": index, "progress": event})
                                update_item(item, self._update_stop, lambda m: self._emit_update_log(m), progress=progress)
                        except UpdateCancelled as exc:
                            item.status = "已取消"
                            item.detail = str(exc)
                            self._emit_update_log(f"{item.name}：{exc}")
                        except Exception as exc:
                            item.status = "更新失败"
                            item.detail = str(exc)
                            self._emit_update_log(f"{item.name}：{exc}")
                        self._update_items[row_idx] = item
                        self._push_event("update_item", self._serialize_update_item(row_idx, item))
                ready = not self._update_stop.is_set() and all(
                    item.kind == "unsupported" or item.status in READY_UPDATE_STATUSES
                    for _, item in selected
                )
            except Exception as exc:
                self._emit_update_log(f"更新检查失败：{exc}")
            finally:
                with self._lock:
                    ready = ready and not self._update_stop.is_set() and not self._close_requested
                    if mode == "prepare":
                        ready = ready and signature == self._launch_signature()
                if mode == "prepare" and not ready and not self._update_stop.is_set():
                    checked_items = [self._update_items[index] for index, _ in selected]
                    lines = [f"{item.name}：{item.status}" for item in checked_items
                             if item.kind != "unsupported" and item.status not in READY_UPDATE_STATUSES]
                    body = "\n".join(lines) or "启动前检查未完成。"
                    description = "启动前检查未通过，任务队列未开始。"
                    self._notify_by_mail(
                        mail_settings, "简小松 · 本轮未启动", body,
                        html_body=render_mail("本轮未启动", description, [
                            (item.name, "stopped" if item.status == "已取消" else "update", item.status, "")
                            for item in checked_items
                            if item.kind != "unsupported" and item.status not in READY_UPDATE_STATUSES
                        ]),
                    )
                with self._lock:
                    ready = ready and not self._update_stop.is_set() and not self._close_requested
                    if mode == "prepare":
                        ready = ready and signature == self._launch_signature()
                        if ready:
                            self._prepared_update_ticket = (signature, time.monotonic())
                    self._update_busy = False
                    canceled = self._update_stop.is_set()
                    if agent_launch and not ready:
                        self._status_text = "已取消启动" if canceled else "脚本未就绪，任务未启动"
                        self._push_event("runner", self._running_snapshot())
                    self._push_event("update_done", {
                        "mode": mode, "ready": ready, "canceled": canceled,
                        "agentLaunch": agent_launch,
                        "deferred": [item.name for _, item in selected if item.status == "沿用本地版本"],
                        "inAppUpdates": [f"{item.name} {item.latest}" for _, item in selected if item.status == MAA_UPDATE_IN_APP],
                        "unsupported": [item.name for _, item in selected if item.kind == "unsupported"],
                    })
                    if agent_launch and ready:
                        self.start_tasks()
                if self._close_requested:
                    self.win_close()

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True}

    def _emit_update_log(self, msg: str) -> None:
        self._append_log(msg)
        self._push_event("update_log", {"line": msg})

    def updates_stop(self) -> None:
        with self._lock:
            self._prepared_update_ticket = None
            self._update_stop.set()

    def updates_open_tool(self, index: int) -> Dict[str, Any]:
        if not (0 <= index < len(self._update_items)):
            return {"ok": False, "error": "未选中脚本"}
        item = self._update_items[index]
        exe = item.executable
        if exe.is_file():
            target = exe.parent / "March7th Launcher.exe" if getattr(item, "key", "") == "march7th" else exe
            try:
                native_popen([str(target)], cwd=str(target.parent))
                return {"ok": True}
            except OSError as exc:
                return {"ok": False, "error": f"打开原工具失败：{exc}"}
        return {"ok": False, "error": "找不到可执行文件"}

    # -------------------- 统计、日志与外部目录 --------------------

    def get_script_report_history(self) -> List[Dict[str, Any]]:
        with self._lock:
            tasks = list(self._config.tasks)
        reports = [{"taskId": key, **report}
                   for key, report in iter_script_reports(self._stats_manager.stats_dir, tasks)]
        return sorted(reversed(reports), key=lambda report: report["time"], reverse=True)

    def get_stats(self) -> Dict[str, Any]:
        return self._read_stats_payload()

    def clear_logs(self) -> bool:
        with self._lock:
            self._logs_history.clear()
        return True

    def open_folder(self, which: str) -> bool:
        mapping = {
            "base": self._base_dir,
            "logs": self._file_logger.log_dir,
            "stats": self._base_dir / "runtime_stats",
        }
        target = mapping.get(which, self._base_dir)
        target.mkdir(parents=True, exist_ok=True)
        try:
            if platform.system().lower() == "windows":
                os.startfile(str(target))  # type: ignore[attr-defined]
            else:
                webbrowser.open(target.as_uri())
            return True
        except Exception:
            return False

    def check_app_update(self) -> Dict[str, Any]:
        return check_app_release()

    def open_script_catalog(self, confirmed: bool = False, remember: bool = False) -> Dict[str, Any]:
        preference = "skip_script_catalog_notice"
        with self._lock:
            if not self._config.extra_settings.get(preference):
                now = time.monotonic()
                if not confirmed:
                    self._script_catalog_notice_ready_at = now + SCRIPT_CATALOG_NOTICE_SECONDS
                    return {"noticeRequired": True, "waitSeconds": SCRIPT_CATALOG_NOTICE_SECONDS}
                if self._script_catalog_notice_ready_at is None or now < self._script_catalog_notice_ready_at:
                    return {"ok": False, "error": "请先阅读来源提示，并等待 7 秒。"}
        try:
            opened = webbrowser.open(SCRIPT_CATALOG_URL)
        except (OSError, webbrowser.Error):
            opened = False
        if not opened:
            return {"ok": False, "error": "无法打开默认浏览器，请稍后重试。"}
        with self._lock:
            self._script_catalog_notice_ready_at = None
            if confirmed and remember:
                previous = self._config.extra_settings
                self._config.extra_settings = {**previous, preference: True}
                try:
                    self._config_manager.save(self._config)
                except OSError:
                    self._config.extra_settings = previous
                    return {"ok": True, "warning": "已打开项目列表，但未能保存「不再提示」。"}
        return {"ok": True}

    def open_external(self, target: str) -> bool:
        if target == "docs":
            doc = resolve_doc_file(self._base_dir, "index.html")
            if doc:
                webbrowser.open(doc.as_uri())
                return True
            return False
        if target == "author":
            webbrowser.open(AUTHOR_URL)
            return True
        if target == "github":
            webbrowser.open(GITHUB_URL)
            return True
        if target == "project":
            return webbrowser.open(PROJECT_URL)
        if target == "releases":
            return webbrowser.open(RELEASES_URL)
        if target.startswith(("http://", "https://")):
            webbrowser.open(target)
            return True
        return False
