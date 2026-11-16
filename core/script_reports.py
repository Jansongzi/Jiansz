"""将运行统计和执行器事件转换为启动中心的脚本报告。"""
import json
from collections import defaultdict
from datetime import date
from pathlib import Path, PureWindowsPath

def report_payload(record) -> dict:
    status = str(record.get("status", ""))
    result = str(record.get("result", ""))
    if result not in {"success", "failed", "unknown", "stopped", "running"}:
        if status.startswith("日志确认完成"):
            result = "success"
        elif any(word in status for word in ("失败", "异常", "超时")):
            result = "failed"
        elif status.startswith("已停止"):
            result = "stopped"
        else:
            result = "unknown"
    return {
        "taskName": str(record.get("task_name") or ""),
        "result": result,
        "time": str(record.get("time") or ""),
        "elapsed": str(record.get("elapsed_text") or ""),
        "status": status,
        "source": str(record.get("source") or ""),
    }


def iter_script_reports(stats_directory: Path, tasks):
    by_name = defaultdict(list)
    for task in tasks:
        by_name[task.name.strip().casefold()].append(task)

    for path in sorted(stats_directory.glob("session_*.json")):
        try:
            records = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            key = record.get("task_id")
            if not isinstance(key, str) or not key:
                name = str(record.get("task_name") or "").strip().casefold()
                candidates = by_name.get(name, [])
                if record.get("script_path"):
                    script = PureWindowsPath(str(record["script_path"]))
                    candidates = [task for task in candidates if PureWindowsPath(task.script_path) == script]
                key = candidates[0].task_id if len(candidates) == 1 else ""
            yield key, report_payload(record)


def load_script_reports(stats_directory: Path, tasks, *, day: date) -> dict:
    """保留当天每个任务最近的一次结果，按结果时间归入当天。"""
    reports = {}
    prefix = day.isoformat()
    for key, report in iter_script_reports(stats_directory, tasks):
        if (key and report["time"][:10] == prefix
                and (key not in reports or report["time"] >= reports[key]["time"])):
            reports[key] = report
    return reports
