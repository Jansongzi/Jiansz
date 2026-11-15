from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from core import FileLogger, RuntimeStatsManager, ScriptRunnerWorker, TaskConfig
from core.task_results import TaskLogProbe, TaskResult, _onedragon, _okww


class TaskLogResultsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def file(self, relative, content=""):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_bettergi_failure_overrides_normal_end_and_ignores_previous_run(self):
        header = "[18:40:00.001] [INF] [Primary:S1:P123:T456] BetterGenshinImpact.ViewModel.Pages.OneDragonFlowViewModel\n"
        finish = header + "一条龙和配置组任务结束\n"
        logfile = self.file("log/better-genshin-impact20261114.log", header + "一条龙任务执行: 1/1\n" + finish)
        probe = TaskLogProbe.capture(self.root / "BetterGI.exe")
        self.assertEqual(probe.inspect({123}).status, "unknown")
        failure = ("[18:50:00.001] [ERR] [Primary:S1:P123:T456] BetterGenshinImpact.GameTask.TaskRunner\n"
                   "自动地脉花执行失败: 无法领取奖励\n")
        with logfile.open("a", encoding="utf-8") as file:
            file.write(header + "一条龙任务执行: 1/1\n" + failure + finish)
        self.assertEqual(probe.inspect({123}).status, "failed")
        self.assertEqual(probe.inspect({222}).status, "unknown")

    def test_march7th_waits_for_matching_run_to_finalize(self):
        probe = TaskLogProbe.capture(self.root / "March7th Assistant.exe")
        run = {"pid": 123, "started_at": datetime.now().astimezone().isoformat(), "status": "running"}
        self.file("logs/runs/fixture.json", json.dumps(run))
        self.assertTrue(probe.inspect({123}).pending)
        with_end = {**run, "status": "failed", "exit_code": 1, "ended_at": run["started_at"]}
        self.file("logs/runs/fixture.json", json.dumps(with_end))
        self.assertEqual(probe.inspect({123}).status, "failed")
        self.assertTrue(probe.inspect({222}).pending)

    def test_old_okww_success_does_not_carry_over_after_rotation(self):
        logs = "data/apps/ok-ww/working/logs/"
        old = "2026-11-14 INFO MainThread ok:ok-script init v3.8.1 pid=12\n"
        success = "2026-11-14 INFO TaskExecutor TaskExecutor:Successfully Executed Task, Exiting Game and App!\n"
        path = self.file(logs + "ok-script.log", old + success)
        probe = TaskLogProbe.capture(self.root / "ok-ww.exe")
        path.rename(path.with_name("ok-script.2026-11-14.log"))
        self.file(logs + "ok-script.log", old.replace("pid=12", "pid=34"))
        self.assertEqual(probe.inspect({34}).status, "unknown")

    def test_okww_task_exception_is_not_success(self):
        lines = ["2026-11-14 INFO MainThread ok:ok-script init v3.8.1 pid=12",
                 "2026-11-14 ERROR TaskExecutor TaskExecutor:Daily Task exception stopped Traceback",
                 "2026-11-14 INFO TaskExecutor TaskExecutor:Successfully Executed Task, Exiting Game and App!"]
        self.assertEqual(_okww([("ok-script.log", line) for line in lines], {12}).status, "failed")

    def test_onedragon_child_failure_survives_group_end(self):
        lines = [
            "[13:00:00.000] [operation.py 451] [INFO]: 指令[ 执行应用组 one_dragon ] 节点 检测游戏窗口 返回状态 成功",
            "[13:00:02.000] [operation.py 702] [ERROR]: 指令[ 每日签到 ] 执行失败 返回状态 未领取",
            "[13:00:02.100] [operation.py 451] [INFO]: 指令[ 执行应用组 one_dragon ] 节点 执行应用 -> 执行应用 返回状态 下一个",
            "[13:00:20.000] [operation.py 700] [INFO]: 指令[ 执行应用组 one_dragon ] 执行成功 返回状态 全部结束",
            "[13:00:21.000] [operation.py 700] [INFO]: 指令[ 一条龙 ] 执行成功 返回状态 全部结束",
        ]
        self.assertEqual(_onedragon([("log.txt", line) for line in lines]).status, "failed")

    def test_runner_records_log_failure_with_zero_exit_code(self):
        exe = self.file("BetterGI.exe")
        task = TaskConfig.from_dict({"name": "test", "script_path": str(exe), "enabled": True}, 1)
        stats = RuntimeStatsManager(self.root)
        worker = ScriptRunnerWorker([task], False, 60, FileLogger(self.root / "logs"), stats)
        process = Mock(pid=123, poll=Mock(return_value=0))
        with patch.object(worker, "_start_process", return_value=process), \
             patch.object(worker, "_read_task_result", return_value=TaskResult("failed", "测试失败", "log.txt")):
            worker._run_one_task(task)
        self.assertTrue(worker.had_task_error)
        self.assertIn("日志确认失败", stats.records[0]["status"])
