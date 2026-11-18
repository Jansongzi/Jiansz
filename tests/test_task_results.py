from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from core import FileLogger, RuntimeStatsManager, ScriptRunnerWorker, TaskConfig
from core.task_results import (
    TaskLogProbe, daily_result, _active_march7th_weekly, _active_okww_weekly,
    _active_onedragon_weekly, _onedragon, _okww,
)


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

    def test_bettergi_confirms_daily_reward_despite_ley_line_failure(self):
        header = "[18:40:00.001] [INF] [Primary:S1:P123:T456] BetterGenshinImpact.GameTask.Common.TaskControl\n"
        reward = header + '检查每日奖励结果："今日奖励已领取"\n'
        logfile = self.file("log/better-genshin-impact20261114.log", reward)
        probe = TaskLogProbe.capture(self.root / "BetterGI.exe")
        self.assertEqual(probe.inspect({123}).status, "unknown")
        failure = ("[18:50:00.001] [ERR] [Primary:S1:P123:T456] BetterGenshinImpact.GameTask.TaskRunner\n"
                   "自动地脉花执行失败: 无法领取奖励\n")
        with logfile.open("a", encoding="utf-8") as file:
            file.write(failure + reward)
        self.assertEqual(probe.inspect({123}), daily_result(True, True, logfile.name))
        self.assertEqual(probe.inspect({222}).status, "unknown")

    @staticmethod
    def maa_log(previous_task):
        callbacks = [
            ("TaskChainStart", {}),
            ("SubTaskCompleted", {"details": {"task": "ReceiveAward"}, "pre_task": previous_task}),
            ("TaskChainCompleted", {}),
            ("AllTasksCompleted", {"finished_tasks": [6]}),
        ]
        header = "[2026-11-17 13:17:23.750][INF][Px123][Tx456] Assistant::append_callback |"
        return "".join(
            f"{header} {callback} {json.dumps(dict(taskchain='Award', taskid=6, uuid='test', **fields))}\n"
            for callback, fields in callbacks
        )

    def test_maa_requires_fresh_daily_reward_and_matching_pid(self):
        log = self.file("debug/asst.log", self.maa_log("DailyTask"))
        probe = TaskLogProbe.capture(self.root / "MAA.exe")
        self.assertEqual(probe.inspect({123}).status, "unknown")
        with log.open("a", encoding="utf-8") as file:
            file.write(self.maa_log("WeeklyTask"))
        self.assertEqual(probe.inspect({123}), daily_result(source="asst.log"))
        with log.open("a", encoding="utf-8") as file:
            file.write(self.maa_log("Award"))
        self.assertEqual(probe.inspect({456}).status, "unknown")
        self.assertEqual(probe.inspect({123}), daily_result(True, True, "asst.log"))

    def test_maa_exit_uses_log_even_with_configured_simulator_window(self):
        exe = self.file("MAA.exe")
        task = TaskConfig.from_dict(
            {"name": "MAA", "script_path": str(exe), "enabled": True, "window_keywords": ["mumu"]}, 1,
        )
        stats = RuntimeStatsManager(self.root)
        worker = ScriptRunnerWorker([task], False, 60, FileLogger(self.root / "logs"), stats)

        def launch(*args):
            self.file("debug/asst.log", self.maa_log("DailyTask"))
            return Mock(pid=123, poll=Mock(return_value=0))

        with patch.object(worker, "_start_process", side_effect=launch), \
             patch.object(worker, "_confirm_launch_success", return_value=True), \
             patch.object(worker, "_is_monitor_present") as monitor:
            self.assertEqual(worker._run_one_task(task), "continue")
        monitor.assert_not_called()
        self.assertEqual(stats.records[0]["result"], "success")
        self.assertEqual(stats.records[0]["source"], "asst.log")

    def test_march7th_daily_success_ignores_nonzero_exit(self):
        probe = TaskLogProbe.capture(self.root / "March7th Assistant.exe")
        run = {"pid": 123, "started_at": datetime.now().astimezone().isoformat(), "status": "running"}
        self.file("logs/runs/fixture.json", json.dumps(run))
        self.assertTrue(probe.inspect({123}).pending)
        with_end = {**run, "status": "failed", "exit_code": 1, "ended_at": run["started_at"]}
        self.file("logs/runs/fixture.json", json.dumps(with_end))
        self.file("logs/runs/fixture.log", "每日实训活跃度 500/500\n")
        self.assertEqual(probe.inspect({123}), daily_result(True, source="fixture.log"))
        self.file("logs/runs/fixture.log", "每日实训活跃度 500/500\n每日实训已完成\nEOFError\n")
        self.assertEqual(probe.inspect({123}), daily_result(True, True, "fixture.log"))
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

    def test_okww_clicking_reward_is_not_claim_confirmation(self):
        lines = ["2026-11-14 INFO MainThread ok:ok-script init v3.8.1 pid=12",
                 "2026-11-14 INFO TaskExecutor DailyTask:info_set total daily points 100",
                 "2026-11-14 INFO TaskExecutor DailyTask:通过坐标领取每日奖励",
                 "2026-11-14 ERROR TaskExecutor TaskExecutor:Weekly Task exception stopped Traceback"]
        self.assertEqual(_okww([("ok-script.log", line) for line in lines], {12}),
                         daily_result(True, source="ok-script.log", pending=True))

    def test_okww_recognizes_completed_reward_flow_in_both_locales(self):
        for action, completion in (
            ("通过坐标领取每日奖励", "每日任务已完成"),
            ("claim daily reward via  coordinate", "Daily Task Completed"),
        ):
            with self.subTest(action=action):
                lines = [
                    "2026-11-18 INFO MainThread ok:ok-script init v3.8.4 pid=12",
                    "2026-11-18 INFO TaskExecutor DailyTask:info_set current task claim daily",
                    f"2026-11-18 INFO TaskExecutor DailyTask:{action}",
                    "2026-11-18 INFO TaskExecutor DailyTask:info_set current task wait main esc=True",
                    "2026-11-18 INFO TaskExecutor DailyTask:info_set current task in main esc=True",
                    "2026-11-18 INFO TaskExecutor DailyTask:info_set current task claim mail",
                    f"2026-11-18 INFO TaskExecutor DailyTask:{completion}",
                ]
                self.assertEqual(_okww([("ok-script.log", line) for line in lines], {12}),
                                 daily_result(True, True, "ok-script.log"))

    def test_okww_reward_does_not_carry_over_from_previous_process(self):
        lines = [
            "2026-11-18 INFO MainThread ok:ok-script init v3.8.4 pid=12",
            "2026-11-18 INFO TaskExecutor DailyTask:通过坐标领取每日奖励",
            "2026-11-18 INFO TaskExecutor DailyTask:info_set current task claim mail",
            "2026-11-18 INFO TaskExecutor DailyTask:每日任务已完成",
            "2026-11-18 INFO MainThread ok:ok-script init v3.8.4 pid=34",
            "2026-11-18 INFO TaskExecutor DailyTask:info_set total daily points 100",
            "2026-11-18 ERROR TaskExecutor DailyTask:领取过程失败",
        ]
        self.assertEqual(_okww([("ok-script.log", line) for line in lines], {34}),
                         daily_result(True, source="ok-script.log", pending=True))

    def test_onedragon_checks_daily_activity_despite_other_failure(self):
        lines = [
            "[13:00:00.000] [operation.py 451] [INFO]: 指令[ 执行应用组 one_dragon ] 节点 检测游戏窗口 返回状态 成功",
            "[13:00:02.000] [operation.py 702] [ERROR]: 指令[ 每日签到 ] 执行失败 返回状态 未领取",
            "[13:00:02.100] [operation.py 451] [INFO]: 指令[ 执行应用组 one_dragon ] 节点 执行应用 -> 执行应用 返回状态 下一个",
            "[13:00:10.000] [operation.py 451] [INFO]: 指令[ 活跃度奖励 ] 节点 点击奖励 -> 查看奖励结果 返回状态 日常奖励已领取或活跃度未满",
            "[13:00:11.000] [operation.py 451] [INFO]: 指令[ 活跃度奖励 ] 节点 查看奖励结果 -> 识别活跃度 返回状态 活跃度已满",
            "[13:00:20.000] [operation.py 700] [INFO]: 指令[ 执行应用组 one_dragon ] 执行成功 返回状态 全部结束",
            "[13:00:21.000] [operation.py 700] [INFO]: 指令[ 一条龙 ] 执行成功 返回状态 全部结束",
        ]
        self.assertEqual(_onedragon([("log.txt", line) for line in lines]), daily_result(True, True, "log.txt"))

    def test_runner_only_reports_daily_failure(self):
        exe = self.file("BetterGI.exe")
        task = TaskConfig.from_dict({"name": "test", "script_path": str(exe), "enabled": True}, 1)
        stats = RuntimeStatsManager(self.root)
        worker = ScriptRunnerWorker([task], False, 60, FileLogger(self.root / "logs"), stats)
        process = Mock(pid=123, poll=Mock(return_value=0))
        with patch.object(worker, "_start_process", return_value=process), \
             patch.object(worker, "_read_task_result", return_value=daily_result(True, False, "log.txt")):
            worker._run_one_task(task)
        self.assertTrue(worker.had_task_error)
        self.assertIn("每日奖励未领取", stats.records[0]["status"])

    def test_weekly_progress_must_be_recent_and_not_finished(self):
        now = datetime(2026, 11, 16, 14, 1, 31)
        garden = "2026-11-16 14:01:28,551 INFO TaskExecutor GardenTask:点击 garden_next_day"
        self.assertEqual(_active_okww_weekly([("ok-script.log", garden)], now), "鸣潮每周乐园")
        self.assertEqual(_active_okww_weekly([("ok-script.log", garden)], now + timedelta(minutes=4)), "")
        march = "2026-11-16 14:01:28,000 | INFO | 开始「差分宇宙」"
        self.assertEqual(_active_march7th_weekly(march, now), "差分宇宙")
        self.assertEqual(_active_march7th_weekly(march + "\n------ 完成 ------", now), "")
        zenless = "[14:01:28.551] [operation.py 451] [INFO]: 指令[ 恶名狩猎 ] 节点 自动战斗 返回状态 成功"
        next_application = "[14:01:29.000] [operation.py 451] [INFO]: 指令[ 执行应用组 one_dragon ] 节点 执行应用 返回状态 下一个"
        self.assertEqual(_active_onedragon_weekly([("log.txt", zenless)], now), "恶名狩猎")
        self.assertEqual(_active_onedragon_weekly([("log.txt", zenless), ("log.txt", next_application)], now), "")

    def test_weekly_timeout_extension_applies_once(self):
        exe = self.file("ok-ww.exe")
        task = TaskConfig.from_dict({"name": "test", "script_path": str(exe), "enabled": True,
                                     "timeout_minutes": 1}, 1)
        stats = RuntimeStatsManager(self.root)
        worker = ScriptRunnerWorker([task], False, 60, FileLogger(self.root / "logs"), stats)
        process = Mock(pid=123, poll=Mock(return_value=None))
        probe = Mock()
        probe.active_weekly_task.return_value = "鸣潮每周乐园"
        probe.inspect.return_value = daily_result()
        with patch.object(worker, "_start_process", return_value=process), \
             patch.object(worker, "_confirm_launch_success", return_value=True), \
             patch.object(worker, "_active_time", side_effect=[0, 60, 1860]), \
             patch.object(worker, "_handle_timeout", return_value="continue") as timeout, \
             patch.object(TaskLogProbe, "capture", return_value=probe):
            self.assertEqual(worker._run_one_task(task), "continue")
        probe.active_weekly_task.assert_called_once()
        timeout.assert_called_once()
        self.assertEqual(stats.records[0]["elapsed_seconds"], 31 * 60)
