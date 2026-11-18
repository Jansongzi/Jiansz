"""为当前 Windows 用户注册简小松的每日启动任务。"""
from datetime import datetime, timedelta
from hashlib import sha256
import os
from pathlib import Path
import re

import pythoncom
import pywintypes
import win32api
from win32com.client import Dispatch

from core.agent_pipe import _user_sid

_TASK_TRIGGER_DAILY = 2
_TASK_TRIGGER_LOGON = 9
_TASK_ACTION_EXEC = 0
_TASK_CREATE_OR_UPDATE = 6
_TASK_LOGON_INTERACTIVE_TOKEN = 3
_TASK_RUNLEVEL_HIGHEST = 1
_TASK_INSTANCES_PARALLEL = 0
_DESCRIPTION = "简小松 · 每日定时启动已启用的任务"
_STARTUP_DESCRIPTION = "简小松 · 登录 Windows 后打开调度助手"


class ScheduleError(RuntimeError):
    pass


def _error_code(error):
    # IDispatch may put the scheduler's HRESULT in EXCEPINFO instead of hresult.
    return (error.excepinfo[5] if error.excepinfo and error.excepinfo[5] else error.hresult) & 0xffffffff


class DailySchedule:
    def __init__(self, base_dir: Path):
        self.executable = base_dir.resolve() / "Jiansz.exe"

    def _with_scheduler(self, operation, prefix="Jiansz_Daily"):
        pythoncom.CoInitialize()
        try:
            return self._call_scheduler(operation, prefix)
        except ScheduleError as exc:
            message = str(exc)
        except pywintypes.com_error as exc:
            hresult = _error_code(exc)
            if hresult == 0x80070005:
                message = "Windows 拒绝访问计划任务。请用管理员权限运行简小松后重试。"
            else:
                message = f"Windows 任务计划程序操作失败（0x{hresult:08X}）。"
        except pywintypes.error:
            message = "无法读取当前 Windows 用户。"
        finally:
            pythoncom.CoUninitialize()
        raise ScheduleError(message)

    def _call_scheduler(self, operation, prefix):
        try:
            account = win32api.GetUserNameEx(2)  # NameSamCompatible
        except pywintypes.error:
            account = _user_sid()
        key = f"{account.casefold()}|{os.path.normcase(str(self.executable))}"
        name = f"{prefix}_{sha256(key.encode('utf-8')).hexdigest()[:16]}"
        service = Dispatch("Schedule.Service")
        service.Connect()
        return operation(service, service.GetFolder("\\"), name, account)

    def _read_task(self, folder, name, description=_DESCRIPTION, trigger_type=_TASK_TRIGGER_DAILY,
                   arguments="--start"):
        try:
            task = folder.GetTask(name)
        except pywintypes.com_error as exc:
            if _error_code(exc) == 0x80070002:
                return None
            raise
        definition = task.Definition
        if (definition.RegistrationInfo.Description != description
                or definition.Triggers.Count != 1 or definition.Triggers.Item(1).Type != trigger_type
                or definition.Actions.Count != 1 or definition.Actions.Item(1).Type != _TASK_ACTION_EXEC):
            raise ScheduleError("发现同名的 Windows 任务，无法确认它属于简小松。")
        action = definition.Actions.Item(1)
        if (os.path.normcase(action.Path) != os.path.normcase(str(self.executable))
                or action.Arguments != arguments):
            raise ScheduleError("现有定时任务的启动命令与当前安装不一致。")
        return task

    @staticmethod
    def _state(task):
        if task is None:
            return {"enabled": False, "time": "08:00", "nextRun": ""}
        trigger = task.Definition.Triggers.Item(1)
        enabled = bool(task.Enabled and trigger.Enabled)
        next_run = task.NextRunTime if enabled else None
        return {
            "enabled": enabled,
            "time": datetime.fromisoformat(trigger.StartBoundary).strftime("%H:%M"),
            "nextRun": next_run.strftime("%Y-%m-%d %H:%M") if next_run and next_run.year >= 2000 else "",
        }

    def get(self):
        return self._with_scheduler(lambda _, folder, name, _account: self._state(self._read_task(folder, name)))

    def _definition(self, service, account, description, arguments):
        if not self.executable.is_file():
            raise ScheduleError("未找到 Jiansz.exe，请在已安装的简小松中设置 Windows 启动计划。")
        definition = service.NewTask(0)
        definition.RegistrationInfo.Description = description
        definition.Principal.UserId = account
        definition.Principal.LogonType = _TASK_LOGON_INTERACTIVE_TOKEN
        definition.Principal.RunLevel = _TASK_RUNLEVEL_HIGHEST
        settings = definition.Settings
        settings.Enabled = True
        settings.ExecutionTimeLimit = "PT0S"
        settings.StartWhenAvailable = False
        settings.DisallowStartIfOnBatteries = False
        settings.StopIfGoingOnBatteries = False
        # 上次由计划启动的主窗口可能仍开着，新实例通过本机入口判断是否已有队列。
        settings.MultipleInstances = _TASK_INSTANCES_PARALLEL
        action = definition.Actions.Create(_TASK_ACTION_EXEC)
        action.Path = str(self.executable)
        action.WorkingDirectory = str(self.executable.parent)
        action.Arguments = arguments
        return definition

    def save_startup(self, enabled: bool):
        def apply(service, folder, name, account):
            existing = self._read_task(folder, name, _STARTUP_DESCRIPTION, _TASK_TRIGGER_LOGON, "")
            if not enabled:
                if existing is not None:
                    existing.Enabled = False
                return
            if existing is not None and existing.Definition.Triggers.Item(1).Enabled:
                if not existing.Enabled:
                    existing.Enabled = True
                return
            definition = self._definition(service, account, _STARTUP_DESCRIPTION, "")
            trigger = definition.Triggers.Create(_TASK_TRIGGER_LOGON)
            trigger.UserId = account
            trigger.Enabled = True
            folder.RegisterTaskDefinition(
                name, definition, _TASK_CREATE_OR_UPDATE, account, None, _TASK_LOGON_INTERACTIVE_TOKEN
            )
        self._with_scheduler(apply, "Jiansz_Logon")

    def save(self, enabled: bool, clock: str):
        if enabled and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", clock, flags=re.ASCII):
            raise ScheduleError("请选择有效的启动时间。")
        def apply(service, folder, name, account):
            existing = self._read_task(folder, name)
            if not enabled:
                if existing is not None:
                    existing.Enabled = False
                return self._state(existing)
            now = datetime.now()
            hour, minute = map(int, clock.split(":"))
            first = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if first <= now:
                first += timedelta(days=1)

            definition = self._definition(service, account, _DESCRIPTION, "--start")
            trigger = definition.Triggers.Create(_TASK_TRIGGER_DAILY)
            trigger.StartBoundary = first.isoformat(timespec="seconds")
            trigger.DaysInterval = 1
            trigger.Enabled = True

            registered = folder.RegisterTaskDefinition(
                name, definition, _TASK_CREATE_OR_UPDATE, account, None, _TASK_LOGON_INTERACTIVE_TOKEN
            )
            return self._state(registered)

        return self._with_scheduler(apply)
