"""从本轮新增的脚本运行记录判断结果。"""
from dataclasses import dataclass
from datetime import datetime
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
        self.old_paths = set()
        for path in self._paths():
            self.old_paths.add(path)
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
        truncated = False
        paths = self._paths()
        identities = set()
        for path in paths:
            try:
                stat = path.stat()
                identities.add((stat.st_dev, stat.st_ino))
            except OSError:
                continue
        for path in paths:
            try:
                with path.open("rb") as file:
                    stat = path.stat()
                    if stat.st_mtime < self.started - 1:
                        continue
                    cursor = self.baseline.get((stat.st_dev, stat.st_ino))
                    if path in self.old_paths and cursor is None and not self.baseline.keys() <= identities:
                        truncated = True
                    offset = 0
                    if cursor:
                        previous_size, position, fingerprint = cursor
                        file.seek(position)
                        old_bytes = file.read(previous_size - position)
                        if hashlib.sha256(old_bytes).digest() == fingerprint:
                            offset = previous_size
                        else:
                            truncated = True
                    if stat.st_size - offset > _MAX_BYTES:
                        truncated = True
                        offset = stat.st_size - _MAX_BYTES
                    file.seek(offset)
                    content = file.read(_MAX_BYTES)
                lines.extend((path.name, line) for line in content.decode("utf-8-sig", errors="replace").splitlines())
            except OSError:
                truncated = True
        return lines, truncated

    def inspect(self, pids):
        if self.kind == "march7th":
            return self._march7th(pids)
        lines, truncated = self._new_lines()
        if self.kind == "bettergi":
            result = _bettergi(lines, pids)
        elif self.kind == "okww":
            result = _okww(lines, pids)
        else:
            result = _onedragon(lines)
        if truncated and result.status == "success":
            return TaskResult("unknown", "日志发生覆盖或缺失，无法核对整轮记录。", result.source, True)
        return result

    def _march7th(self, pids):
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
            return TaskResult("unknown", "未找到与本次启动匹配的三月七运行记录。", pending=True)
        _, path, data = max(matching, key=lambda item: item[0])
        status = data.get("status")
        if status == "success" and data.get("exit_code") in (0, None) and data.get("ended_at"):
            return TaskResult("success", "三月七运行记录确认完成。", path.name)
        if status in {"failed", "error", "cancelled", "canceled", "stopped", "aborted"} or data.get("exit_code") not in (None, 0):
            return TaskResult("failed", f"三月七运行记录为 {status}，退出码 {data.get('exit_code')}。", path.name)
        return TaskResult("unknown", "三月七尚未写入最终结果。", path.name, True)


def _bettergi(lines, pids):
    header = re.compile(r"^\[[\d:.]+\] \[([A-Z]+)\] \[[^\]]*:P(\d+):[^\]]+\]\s*(.*)")
    owner = None
    has_run = False
    finished = False
    reported_failure = None
    source = ""
    for filename, line in lines:
        match = header.match(line)
        if match:
            owner = match if int(match[2]) in pids else None
            continue
        if not owner or not line.strip():
            continue
        level, _, component = owner.groups()
        if "OneDragonFlowViewModel" in component:
            if re.search(r"一条龙任务执行\s*[:：]\s*1/", line):
                has_run, finished, reported_failure = True, False, None
            if "一条龙和配置组任务结束" in line and has_run:
                finished, source = True, filename
        if has_run and level in {"ERR", "FTL"} and (
            ("GameTask." in component or "OneDragonFlowViewModel" in component)
            and re.search(r"执行失败|无法领取|任务.*(?:失败|异常|取消|中断)|异常退出|^\s*[\w.]+Exception:", line)
        ):
            reported_failure = (line.strip()[:180], filename)
    if reported_failure:
        evidence, source = reported_failure
        return TaskResult("failed", evidence, source)
    if finished:
        return TaskResult("success", "BetterGI 已写入本轮一条龙和配置组结束记录。", source)
    return TaskResult("unknown", "未找到与本次进程匹配的一条龙结束记录。", pending=True)


def _okww(lines, pids):
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
    if len(matched) != 1:
        return TaskResult("unknown", "未能唯一对应本轮 OK-WW 脚本日志。", pending=True)
    completed = None
    failure = None
    for filename, line in matched[0][1]:
        if re.search(r"ERROR TaskExecutor TaskExecutor:.* exception stopped\b", line):
            failure = TaskResult("failed", "OK-WW 记录了任务异常中止。", filename)
        if "INFO TaskExecutor TaskExecutor:TaskDisabledException, continue" in line:
            failure = TaskResult("failed", "OK-WW 记录了任务中止。", filename)
        if re.search(r"INFO TaskExecutor TaskExecutor:Successfully Executed Task, Exiting Game and App!", line):
            completed = TaskResult("success", "OK-WW 执行器确认任务完成。", filename)
    return failure or completed or TaskResult("unknown", "OK-WW 尚未写入任务完成记录。", pending=True)


def _onedragon(lines):
    operation = re.compile(r"^\[[\d:.]+\] \[operation\.py \d+\] \[(?:INFO|ERROR)\]: 指令\[\s*(.*?)\s*\] (执行成功|执行失败) 返回状态 (.*)")
    group = "执行应用组 one_dragon"
    starts = [f"[ {group} ] 节点 检测游戏窗口 返回状态", "[ 一条龙 ] 节点 检测游戏窗口 返回状态"]
    if any(sum(marker in line for _, line in lines) > 1 for marker in starts):
        return TaskResult("unknown", "新增日志包含多次一条龙启动，无法唯一对应本轮。")
    active = False
    completed = None
    unresolved = {}
    last_operations = {}
    subtask_failures = {}
    for filename, line in lines:
        if f"[ {group} ] 节点 检测游戏窗口 返回状态" in line:
            active = True
        if active and f"[ {group} ] 节点 " in line and line.endswith("返回状态 下一个"):
            # 组执行器即使遇到子应用失败也继续。节点之前最后结束的操作是这个应用。
            if last_operations:
                name, result = next(reversed(last_operations.items()))
                if result[0] == "执行失败":
                    subtask_failures[name] = (result[1], filename)
                else:
                    subtask_failures.pop(name, None)
            last_operations.clear()
        match = operation.match(line)
        if not match:
            continue
        name, status, detail = match.groups()
        if active and name not in {group, "一条龙"}:
            last_operations.pop(name, None)
            last_operations[name] = (status, detail)
            if status == "执行失败":
                unresolved[name] = detail
            else:
                unresolved.pop(name, None)
        if name in {group, "一条龙"}:
            if status == "执行失败":
                return TaskResult("failed", f"绝区零一条龙执行失败：{detail[:140]}", filename)
            if name == "一条龙" and active and detail == "全部结束":
                completed = filename
                break
    if subtask_failures:
        name, (detail, filename) = next(reversed(subtask_failures.items()))
        return TaskResult("failed", f"{name}执行失败：{detail[:140]}", filename)
    if completed and unresolved:
        return TaskResult("unknown", "一条龙已结束，但日志中仍有未确认恢复的子操作失败。", completed)
    if completed:
        return TaskResult("success", "绝区零一条龙确认全部结束。", completed)
    return TaskResult("unknown", "绝区零一条龙尚未写入最终结果。", pending=True)
