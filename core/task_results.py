"""从本轮新增的脚本运行记录判断结果。"""
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
import time


_DECODERS = {
    "ok-ww.exe": ("okww", "data/apps/ok-ww/working/logs/ok-script*.log"),
    "march7th assistant.exe": ("march7th", "logs/runs/*.json"),
    "march7th launcher.exe": ("march7th", "logs/runs/*.json"),
    "m7a.exe": ("march7th", "logs/runs/*.json"),
    "bettergi.exe": ("bettergi", "log/better-genshin-impact*.log"),
    "onedragon-runtimelauncher.exe": ("onedragon", ".log/log.txt*"),
    "onedragon-launcher.exe": ("onedragon", ".log/log.txt*"),
}
_MAX_BYTES = 2 * 1024 * 1024


def script_log_kind(filename: str) -> str:
    return _DECODERS.get(Path(filename).name.casefold(), ("", ""))[0]


@dataclass(frozen=True)
class TaskResult:
    status: str
    evidence: str
    source: str = ""
    pending: bool = False


def _mtime(path):
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


class TaskLogProbe:
    """按启动前的文件位置读取增量，兼容日期切分和日志轮转。"""

    @classmethod
    def capture(cls, executable: Path):
        decoder = _DECODERS.get(executable.name.lower())
        return cls(executable.parent, *decoder) if decoder else None

    def __init__(self, root, kind, pattern):
        self.root = root
        self.kind = kind
        self.pattern = pattern
        self.started = time.time()
        self.baseline = {}
        for path in self._paths():
            try:
                with path.open("rb") as file:
                    stat = path.stat()
                    position = max(0, stat.st_size - 128)
                    file.seek(position)
                    fingerprint = hashlib.sha256(file.read(128)).digest()
                self.baseline[(stat.st_dev, stat.st_ino)] = (stat.st_size, position, fingerprint)
            except OSError:
                continue

    def _paths(self):
        try:
            paths = self.root.glob(self.pattern)
            return sorted(paths, key=_mtime)[-12:]
        except OSError:
            return []

    def _new_lines(self):
        lines = []
        for path in self._paths():
            try:
                with path.open("rb") as file:
                    stat = path.stat()
                    if stat.st_mtime < self.started - 1:
                        continue
                    cursor = self.baseline.get((stat.st_dev, stat.st_ino))
                    offset = 0
                    if cursor:
                        previous_size, position, fingerprint = cursor
                        file.seek(position)
                        old_bytes = file.read(previous_size - position)
                        if hashlib.sha256(old_bytes).digest() == fingerprint:
                            offset = previous_size
                    if stat.st_size - offset > _MAX_BYTES:
                        offset = stat.st_size - _MAX_BYTES
                    file.seek(offset)
                    content = file.read(_MAX_BYTES)
                lines.extend((path.name, line) for line in content.decode("utf-8-sig", errors="replace").splitlines())
            except OSError:
                continue
        return lines

    def inspect(self, pids):
        if self.kind == "march7th":
            run = self._march7th_run(pids)
            if run is None:
                return daily_result(pending=True)
            log, data, text = run
            activity = re.findall(r"每日实训活跃度\s*(\d+)/500\b", text)
            completed = True if "500" in activity else (False if activity else None)
            # 上游仅在领取后发送 DailyPracticeCompleted，500/500 本身只代表满活跃度。
            claimed = True if re.search(r"每日实训已完成|每日实训奖励已领取|已领取全部实训奖励", text) else None
            return daily_result(completed, claimed, log.name, pending=not data.get("ended_at"))
        lines = self._new_lines()
        if self.kind == "bettergi":
            return _bettergi(lines, pids)
        if self.kind == "okww":
            return _okww(lines, pids)
        return _onedragon(self._onedragon_lines(lines))

    def _onedragon_lines(self, lines):
        # log.txt 可被每次启动截断或覆盖。只使用本轮时间范围内的节点。
        start = datetime.fromtimestamp(self.started)
        seconds = start.hour * 3600 + start.minute * 60 + start.second
        return [
            (name, line) for name, line in lines
            if (stamp := re.match(r"^\[(\d{2}):(\d{2}):(\d{2})\.", line))
            and -1 <= (int(stamp[1]) * 3600 + int(stamp[2]) * 60 + int(stamp[3])
                       - seconds + 43200) % 86400 - 43200
        ]

    def _march7th_run(self, pids):
        matching = []
        for path in self._paths():
            try:
                if path.stat().st_size > 128 * 1024:
                    continue
                data = json.loads(path.read_text(encoding="utf-8-sig"))
                started = datetime.fromisoformat(data["started_at"]).timestamp()
                if int(data["pid"]) not in pids or started < self.started - 2:
                    continue
                matching.append((started, path, data))
            except (OSError, ValueError, TypeError, KeyError):
                continue
        if not matching:
            return None
        _, path, data = max(matching, key=lambda item: item[0])
        log = path.with_suffix(".log")
        try:
            with log.open("rb") as stream:
                stream.seek(max(0, log.stat().st_size - _MAX_BYTES))
                text = stream.read(_MAX_BYTES).decode("utf-8-sig", errors="replace")
        except OSError:
            text = ""
        return log, data, text

    def active_weekly_task(self, pids):
        """只采用本轮近期日志中的周常活动，普通报错或旧日志不能触发延时。"""
        now = datetime.now()
        if self.kind == "okww":
            lines = _okww_session_lines(self._new_lines(), pids)
            return _active_okww_weekly(lines, now)
        if self.kind == "march7th":
            run = self._march7th_run(pids)
            if run is not None and not run[1].get("ended_at"):
                return _active_march7th_weekly(run[2], now)
        if self.kind == "onedragon":
            return _active_onedragon_weekly(self._onedragon_lines(self._new_lines()), now)
        return ""


def _recent(stamp, now):
    return stamp is not None and timedelta(seconds=-2) <= now - stamp <= timedelta(minutes=3)


def _timestamp(line):
    try:
        return datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _active_okww_weekly(lines, now):
    for _, line in reversed(lines):
        if "TaskExecutor DailyTask:" in line or "TaskExecutor TaskExecutor:Successfully Executed Task" in line:
            return ""
        if re.search(r"\bTaskExecutor GardenTask:", line):
            return "鸣潮每周乐园" if _recent(_timestamp(line), now) else ""
    return ""


_MARCH_WEEKLY_START = re.compile(r"开始(?:刷|挑战)?[「\s]*(历战余响|模拟宇宙|差分宇宙|货币战争)")


def _active_march7th_weekly(text, now):
    active = ""
    stamp = None
    for line in text.splitlines():
        if match := _MARCH_WEEKLY_START.search(line):
            active = match[1]
        elif re.search(r"^-{3,}\s*完成\s*-{3,}$|停止运行|开始领取奖励|开始清体力|开始领取每日实训奖励", line):
            active = ""
        if candidate := _timestamp(line):
            stamp = candidate
    return active if _recent(stamp, now) else ""


_ONEDRAGON_WEEKLY = {"恶名狩猎", "式舆防卫战", "危局强袭战", "零号空洞", "丽都周纪 (领奖励)"}


def _active_onedragon_weekly(lines, now):
    for _, line in reversed(lines):
        match = re.match(r"^\[(\d{2}:\d{2}:\d{2})\.\d+\].*指令\[\s*(.*?)\s*\] (.*)", line)
        if not match:
            continue
        clock, application, detail = match.groups()
        if application in {"执行应用组 one_dragon", "一条龙"} and (
            detail.startswith(("执行成功", "执行失败")) or detail.endswith("返回状态 下一个")
        ):
            return ""
        if application in _ONEDRAGON_WEEKLY:
            try:
                stamp = datetime.fromisoformat(f"{now.date()}T{clock}")
            except ValueError:
                return ""
            if stamp - now > timedelta(hours=12):
                stamp -= timedelta(days=1)
            if not detail.startswith(("执行成功", "执行失败")) and _recent(stamp, now):
                return application
            return ""
    return ""


def daily_result(completed=None, claimed=None, source="", *, pending=False):
    completion = {True: "每日委托已完成", False: "每日委托未完成", None: "每日委托未确认"}[completed]
    reward = {True: "每日奖励已领取", False: "每日奖励未领取", None: "每日奖励未确认"}[claimed]
    if completed is True and claimed is True:
        return TaskResult("success", f"{completion} · {reward}", source)
    if not pending and (completed is False or claimed is False):
        return TaskResult("failed", f"{completion} · {reward}", source)
    return TaskResult("unknown", f"{completion} · {reward}", source, pending)


def _bettergi(lines, pids):
    header = re.compile(r"^\[[\d:.]+\] \[([A-Z]+)\] \[[^\]]*:P(\d+):[^\]]+\]\s*(.*)")
    owner = False
    result = daily_result(pending=True)
    for filename, line in lines:
        match = header.match(line)
        if match:
            owner = int(match[2]) in pids and "GameTask.Common.TaskControl" in match[3]
            continue
        if owner and "检查每日奖励结果" in line:
            if "今日奖励已领取" in line:
                # 冒险家协会的每日委托额外奖励要求四项委托（含历练点）都完成。
                result = daily_result(True, True, filename)
            elif "未领取" in line:
                result = daily_result(claimed=False, source=filename)
    return result


def _okww_session_lines(lines, pids):
    sessions = []
    current = None
    for filename, line in lines:
        if "MainThread ok:ok-script init " in line:
            pid = re.search(r"\bpid=(\d+)\b", line)
            current = (int(pid[1]) if pid else None, [])
            sessions.append(current)
        elif current is not None:
            current[1].append((filename, line))
    matched = [session for session in sessions if session[0] in pids]
    if not matched and len(sessions) == 1:
        matched = sessions
    return matched[0][1] if len(matched) == 1 else []


def _okww(lines, pids):
    completed = claimed = None
    source = ""
    for filename, line in _okww_session_lines(lines, pids):
        if not re.search(r"TaskExecutor (?:MultiAccount)?DailyTask:", line):
            continue
        if re.search(r"每日任务已完成|Daily Task Completed|total daily points 100\b", line):
            completed, source = True, filename
        if re.search(r"每日奖励(?:已领取|领取成功)|已领取每日奖励|Daily rewards? (?:already )?claimed", line, re.I):
            claimed, source = True, filename
    return daily_result(completed, claimed, source, pending=True)


def _onedragon(lines):
    completed = claimed = None
    reward_state = ""
    source = ""
    for filename, line in lines:
        if "指令[ 活跃度奖励 ]" not in line:
            continue
        state = line.partition("返回状态 ")[2]
        if "节点 检测游戏窗口 返回状态" in line:
            completed = claimed = None
            reward_state = ""
            source = filename
        elif state in {"日常奖励领取成功", "日常奖励已领取", "日常奖励已领取或活跃度未满",
                       "领取失败重试次数用完"}:
            reward_state, source = state, filename
        elif state in {"活跃度已满", "活跃度未满"}:
            completed, source = state == "活跃度已满", filename
            if reward_state in {"日常奖励领取成功", "日常奖励已领取"}:
                claimed = True
            elif completed and reward_state == "日常奖励已领取或活跃度未满":
                claimed = True
            elif reward_state == "领取失败重试次数用完":
                claimed = False
    return daily_result(completed, claimed, source, pending=completed is None)
