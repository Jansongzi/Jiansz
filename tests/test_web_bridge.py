# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date
import json
import tempfile
import unittest
import threading
import time
from pathlib import Path
from unittest.mock import patch, Mock

from core import CONFIG_FILE_NAME, TaskConfig
from core.updates import UpdateCancelled
from ui.web_app import create_wsgi_app
from ui.web_backend import JianszWebBridge


class WebBridgeTests(unittest.TestCase):
    def test_qq_mail_alerts_keep_credentials_out_of_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = self._configured_bridge(Path(tmp))
            sender, recipient = "sender@qq.com", "recipient@qq.com"
            with patch("ui.web_backend.store_code") as store, \
                 patch("ui.web_backend.get_code", return_value="examplecredential"), \
                 patch("ui.web_backend.send_mail") as smtp:
                self.assertTrue(bridge.save_mail_alert_settings(True, sender, recipient, "examplecredential")["ok"])
                store.assert_called_once_with(Path(tmp), sender, "examplecredential")
                self.assertNotIn("examplecredential", (Path(tmp) / CONFIG_FILE_NAME).read_text(encoding="utf-8"))
                self.assertTrue(bridge.test_mail_alert()["ok"])
                self.assertEqual(smtp.call_args.args[:3], (sender, recipient, "examplecredential"))

    def test_failed_round_sends_one_summary_for_current_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = self._configured_bridge(Path(tmp))
            bridge._run_mail_context = ({"enabled": True}, "test-session", [("a", "原神"), ("b", "绝区零")])
            bridge._on_script_report({"task_id": "a", "result": "unknown", "status": "游戏已关闭"})
            with patch.object(bridge, "_notify_by_mail") as notify:
                bridge._on_worker_finished(False)
                self.assertIn("test-session", notify.call_args.args[2])
                self.assertIn("原神：结果未确认", notify.call_args.args[2])
                self.assertIn("绝区零：本轮无执行结果", notify.call_args.args[2])
                bridge._on_worker_finished(False)
                notify.assert_called_once()

    def test_successful_round_is_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = self._configured_bridge(Path(tmp))
            bridge._run_mail_context = ({"enabled": True}, "test-session", [("a", "原神")])
            bridge._on_script_report({"task_id": "a", "result": "success", "status": "日志确认完成"})
            with patch.object(bridge, "_notify_by_mail") as notify:
                bridge._on_worker_finished(False)
                notify.assert_not_called()

    @staticmethod
    def _configured_bridge(base):
        bridge = JianszWebBridge(base_dir=base)
        bridge._config.tasks = [TaskConfig.from_dict({"name":"test", "script_path":str(base/"BetterGI.exe"),
                                                     "enabled":True}, fallback_order=1)]
        return bridge

    def test_forced_update_cannot_be_bypassed_with_old_setting_or_skip_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = self._configured_bridge(Path(tmp))
            bridge._config.extra_settings["auto_update_scripts_before_launch"] = False
            result = bridge.start_tasks({"skip_updates": True})
            self.assertTrue(result.get("needPrepareUpdate"))
            self.assertFalse(bridge._is_running)
            self.assertTrue(bridge._serialize_config()["auto_update_scripts_before_launch"])

    def test_prepare_emits_progress_and_launch_permission_is_consumed_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = self._configured_bridge(Path(tmp))
            bridge.updates_discover(True)
            finished = threading.Event()
            push = bridge._push_event
            def capture(kind, payload):
                push(kind, payload)
                if kind == "update_done": finished.set()
            def check(item, stop, log):
                item.current="1.0";item.latest="2.0";item.status="有更新"
                return item
            def update(item, stop, log, progress=None):
                progress({"phase":"下载更新包","percent":50,"completed":5,"total":10,"unit":"bytes","speed":1})
                item.status="已是最新"
                return item
            with patch.object(bridge,"_push_event",side_effect=capture), \
                 patch("ui.web_backend.check_item",side_effect=check), \
                 patch("ui.web_backend.update_item",side_effect=update):
                self.assertTrue(bridge.updates_start("prepare")["ok"])
                self.assertTrue(finished.wait(3))
            events=bridge.drain_events()
            self.assertTrue(any(e["type"]=="update_progress" for e in events))
            self.assertIsNotNone(bridge._prepared_update_ticket)
            with patch("ui.web_backend.ScriptRunnerWorker",return_value=Mock()):
                result=bridge.start_tasks({"skip_updates":True})
            self.assertTrue(result["ok"])
            self.assertIsNone(bridge._prepared_update_ticket)
            bridge._is_running=False
            self.assertTrue(bridge.start_tasks({"skip_updates":True}).get("needPrepareUpdate"))

    def test_cancelled_update_does_not_grant_launch_permission(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge=self._configured_bridge(Path(tmp));bridge.updates_discover(True)
            finished=threading.Event();push=bridge._push_event
            def capture(kind,payload):
                push(kind,payload)
                if kind=="update_done":finished.set()
            def check(item,stop,log):
                item.status="有更新";item.latest="2.0";return item
            with patch.object(bridge,"_push_event",side_effect=capture), \
                 patch("ui.web_backend.check_item",side_effect=check), \
                 patch("ui.web_backend.update_item",side_effect=UpdateCancelled("cancel")):
                bridge.updates_start("prepare")
                self.assertTrue(finished.wait(3))
            self.assertEqual(bridge._update_items[0].status,"已取消")
            self.assertIsNone(bridge._prepared_update_ticket)
            self.assertTrue(bridge.start_tasks({"skip_updates":True}).get("needPrepareUpdate"))

    def test_agent_start_updates_then_dispatches_saved_queue_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = self._configured_bridge(Path(tmp))
            bridge._config.auto_start_tasks = True
            bridge._agent_host = True
            self.assertFalse(bridge.get_bootstrap()["autoStartOnOpen"])
            checking = threading.Event()
            release = threading.Event()
            launched = threading.Event()

            def check(item, stop, log):
                checking.set()
                if not release.wait(3):
                    raise TimeoutError
                item.status = "已是最新"
                return item

            with patch("ui.web_backend.check_item", side_effect=check), \
                 patch("ui.web_backend.ScriptRunnerWorker", return_value=Mock(run=launched.set)) as worker:
                self.assertEqual(bridge._agent_command("start")["status"], "accepted")
                self.assertTrue(checking.wait(3))
                self.assertEqual(bridge._agent_command("start")["status"], "updating")
                worker.assert_not_called()
                release.set()
                self.assertTrue(launched.wait(3))
                self.assertEqual(bridge._agent_command("start")["status"], "running")
                worker.assert_called_once()
            events = bridge.drain_events()
            self.assertTrue(any(e["type"] == "update_done" and e["payload"]["agentLaunch"] for e in events))

    def test_changing_task_after_check_requires_new_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge=self._configured_bridge(Path(tmp))
            bridge._prepared_update_ticket=(bridge._launch_signature(),time.monotonic())
            bridge._config.tasks[0].args="new arguments"
            self.assertTrue(bridge.start_tasks({"skip_updates":True}).get("needPrepareUpdate"))

    def test_busy_discovery_keeps_active_update_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge=self._configured_bridge(Path(tmp));bridge.updates_discover(True)
            previous=bridge._update_items
            bridge._update_busy=True
            self.assertTrue(bridge.updates_discover()["busy"])
            self.assertIs(bridge._update_items,previous)

    @patch("ui.web_backend.date")
    def test_dashboard_restores_todays_reports_and_refreshes_at_midnight(self, report_date):
        report_date.today.return_value = date(2026, 11, 14)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            shared_script = str(base / "ok-ww.exe")
            tasks = [
                TaskConfig(name="鸣潮日常", script_path=shared_script, enabled=True, order=1),
                TaskConfig(name="鸣潮日常", script_path=shared_script, enabled=True, order=2),
                TaskConfig(name="原神", script_path=str(base / "BetterGI.exe"), enabled=False, order=3),
            ]
            first, second, disabled = (task.task_id for task in tasks)
            config_path = base / CONFIG_FILE_NAME
            config_path.write_text(json.dumps({"tasks": [task.to_dict() for task in tasks]}), encoding="utf-8")
            stats = base / "runtime_stats"
            stats.mkdir()
            (stats / "session_20261114_180000.json").write_text(json.dumps([{
                "task_name": "鸣潮日常", "script_path": shared_script,
                "status": "游戏窗口已消失", "time": "2026-11-14 18:00:00",
            }, {
                "task_name": "原神", "status": "游戏窗口已消失", "time": "2026-11-14 18:05:00",
            }]), encoding="utf-8")
            (stats / "session_20261114_190000.json").write_text(json.dumps([{
                "task_id": first, "task_name": "鸣潮日常", "script_path": shared_script,
                "result": "failed", "status": "日志确认失败：任务中止",
                "time": "2026-11-14 19:00:00", "elapsed_text": "02:30", "source": "ok-script.log",
            }]), encoding="utf-8")
            (base / "notes_accounts.bin").write_bytes(b"old credentials fixture")
            bridge = JianszWebBridge(base)
            boot = bridge.get_bootstrap()
            self.assertEqual(boot["scriptReports"][first]["result"], "failed")
            self.assertEqual(boot["scriptReports"][first]["source"], "ok-script.log")
            self.assertNotIn(second, boot["scriptReports"])
            self.assertEqual(boot["scriptReports"][disabled]["result"], "unknown")
            history = bridge.get_script_report_history()
            self.assertEqual([report["taskId"] for report in history], [first, disabled, ""])
            self.assertNotIn("gameStates", boot)
            self.assertFalse(hasattr(bridge, "refresh_game_notes"))
            bridge._on_script_report({"task_id": second, "script_path": shared_script, "result": "running",
                                      "time": "2026-11-14 23:59:30"})
            reports = bridge.get_bootstrap()["scriptReports"]
            self.assertEqual(reports[first]["result"], "failed")
            self.assertEqual(reports[second]["result"], "running")
            renamed = {**boot["config"]["tasks"][0], "name": "鸣潮账号一"}
            saved = bridge.save_config({**boot["config"], "tasks": [boot["config"]["tasks"][1], renamed]})
            self.assertEqual([task["task_id"] for task in saved["config"]["tasks"]], [second, first])
            reopened = JianszWebBridge(base).get_bootstrap()
            self.assertEqual(reopened["scriptReports"][first]["result"], "failed")
            bridge.drain_events()
            report_date.today.return_value = date(2026, 11, 15)
            resets = [event for event in bridge.drain_events() if event["type"] == "script_reports_reset"]
            self.assertEqual(len(resets), 1)
            self.assertEqual(set(resets[0]["payload"]), {second})
            self.assertEqual(resets[0]["payload"][second]["result"], "running")
            self.assertEqual(JianszWebBridge(base).get_bootstrap()["scriptReports"], {})
            self.assertEqual(bridge.get_script_report_history()[0]["time"], "2026-11-14 19:00:00")
            bridge._on_script_report({"task_id": second, "result": "success", "time": "2026-11-15 00:01:00"})
            self.assertEqual(bridge.get_bootstrap()["scriptReports"][second]["result"], "success")

    def test_legacy_task_ids_are_stable_until_config_is_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config_path = base / CONFIG_FILE_NAME
            task = {"name": "重复任务", "script_path": "custom.exe"}
            config_path.write_text(json.dumps({"tasks": [task, task]}), encoding="utf-8")
            first = JianszWebBridge(base).get_bootstrap()["config"]["tasks"]
            second = JianszWebBridge(base).get_bootstrap()["config"]["tasks"]
            self.assertEqual(first, second)
            self.assertNotEqual(first[0]["task_id"], first[1]["task_id"])

    def test_save_config_and_prelaunch_update_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bridge = JianszWebBridge(base_dir=base)
            res = bridge.save_config({
                "theme": "dark",
                "shutdown_after_done": False,
                "shutdown_delay_seconds": 90,
                "auto_exit_after_done": False,
                "auto_start_tasks": False,
                "windows_startup": False,
                "enable_timeout_screenshot": True,
                "auto_update_scripts_before_launch": True,
                "tasks": [
                    {
                        "name": "鸣潮",
                        "script_path": "E:/fake/ok-ww.exe",
                        "args": "-t 1",
                        "use_args": True,
                        "enabled": True,
                        "order": 1,
                        "timeout_minutes": 25,
                    }
                ],
            })
            self.assertTrue(res["ok"])
            self.assertEqual(res["config"]["theme"], "dark")
            self.assertEqual(len(res["config"]["tasks"]), 1)

            # auto_update_scripts_before_launch=True + skip_updates=False 且存在可更新项时，提示前端先打开更新确认
            with patch("ui.web_backend.discover_items", return_value=[object()]):
                start_res = bridge.start_tasks({"skip_updates": False})
            self.assertFalse(start_res["ok"])
            self.assertTrue(start_res.get("needPrepareUpdate"))

    def test_stats_and_wsgi_static_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            stats_dir = base / "runtime_stats"
            stats_dir.mkdir(parents=True, exist_ok=True)
            (stats_dir / "runtime_summary.json").write_text(
                json.dumps({
                    "ok-ww.exe": {
                        "count": 4,
                        "average_seconds": 125,
                        "last_seconds": 110,
                        "last_time": "2026-11-14 08:30:00",
                    }
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            bridge = JianszWebBridge(base_dir=base)
            st = bridge.get_stats()
            self.assertEqual(st["totalRuns"], 4)
            self.assertEqual(st["scriptCount"], 1)
            self.assertEqual(st["rows"][0]["averageText"], "02:05")

            # Bottle WSGI 路由应能正确提供前端静态页面与仓库真实游戏图标
            repo_root = Path(__file__).resolve().parent.parent
            app = create_wsgi_app(repo_root)
            routes = [r.rule for r in app.routes]
            self.assertIn("/", routes)
            self.assertIn("/assets/<filepath:path>", routes)
            self.assertTrue((repo_root / "ui" / "web" / "app.js").is_file())
            self.assertTrue((repo_root / "assets" / "games" / "wuwa_app.png").is_file())


if __name__ == "__main__":
    unittest.main()
