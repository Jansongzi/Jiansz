import json
from pathlib import Path
import subprocess
import tempfile
from threading import Event, Thread
import unittest
from unittest.mock import Mock, patch
import zipfile

from core import FileLogger, RuntimeStatsManager, ScriptRunnerWorker, TaskConfig
from core.updates import UpdateError, UpdateItem, _update_onedragon, discover_items
from ui.web_backend import JianszWebBridge


def task(path, **values):
    return TaskConfig.from_dict(
        {"name": path.stem, "script_path": str(path), "enabled": True, **values}, 1
    )


class FlowRegressions(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def worker(self, *tasks):
        return ScriptRunnerWorker(
            list(tasks), False, 60, FileLogger(self.root / "logs"),
            RuntimeStatsManager(self.root),
        )

    def test_short_lived_launcher_hands_off_to_configured_target(self):
        config = task(self.root / "launcher.exe", window_keywords=["test"])
        worker = self.worker(config)
        process = Mock(pid=777, poll=Mock(return_value=0))
        with patch.object(worker, "_is_monitor_present", return_value=(False, "")):
            self.assertTrue(worker._confirm_launch_success(
                config, process, config.name, worker._active_time(), worker._task_monitor_spec(config)
            ))
        self.assertFalse(worker.had_task_error)

    def test_nonzero_exit_is_recorded_as_failure(self):
        path = self.root / "test.exe"
        path.touch()
        config = task(path, timeout_minutes=0)
        worker = self.worker(config)
        process = Mock(pid=777, poll=Mock(return_value=7))
        with patch.object(worker, "_start_process", return_value=process), \
             patch.object(worker, "_confirm_launch_success", return_value=True):
            worker._run_one_task(config)
        self.assertTrue(worker.had_task_error)
        self.assertIn("异常退出", worker.stats_manager.records[0]["status"])

    def test_stop_during_process_poll_does_not_treat_termination_as_a_failure(self):
        path = self.root / "test.exe"
        path.touch()
        config = task(path, timeout_minutes=0)
        for confirmed in (True, False):
            with self.subTest(already_confirmed=confirmed):
                worker = self.worker(config)

                def poll():
                    worker.request_stop(cleanup=False)
                    return 1

                process = Mock(pid=777, poll=poll)
                with patch.object(worker, "_start_process", return_value=process), \
                     patch.object(worker, "_track_children"), \
                     patch.object(worker, "_cleanup_processes_for_tasks"), \
                     patch.object(worker, "_confirm_launch_success",
                                  wraps=None if confirmed else worker._confirm_launch_success) as confirm:
                    if confirmed:
                        confirm.return_value = True
                    result = worker._run_one_task(config)
                self.assertEqual(result, "stop_requested")
                self.assertFalse(worker.abnormal_events)
                self.assertEqual(worker.stats_manager.records[0]["result"], "stopped")

    def test_cleanup_uses_only_registered_processes(self):
        config = task(self.root / "fake.exe", process_keywords=["unrelated"])
        worker = self.worker(config)
        owned = Mock(pid=777, poll=Mock(return_value=None))
        unrelated = Mock(pid=778, poll=Mock(return_value=None))
        worker._owned_roots[777] = owned
        with patch.object(worker, "_track_children"), patch.object(worker, "_terminate_process") as terminate:
            worker._cleanup_processes_for_tasks([config], [unrelated, owned], include_window_keywords=True)
        terminate.assert_called_once_with(owned)

    def test_pause_time_is_not_charged_to_timeout(self):
        worker = self.worker()
        with patch("core.core.time.monotonic", side_effect=[10, 20, 120, 130, 140]):
            started = worker._active_time()
            worker.request_pause()
            self.assertEqual(worker._active_time() - started, 10)
            worker.request_resume()
            self.assertEqual(worker._active_time() - started, 20)

    def test_batch_from_a_path_with_spaces(self):
        folder = self.root / "portable & test"
        folder.mkdir()
        script = folder / "run daily.cmd"
        script.write_bytes(b"@echo off\r\n>result.txt echo %~1\r\n")
        config = task(script, use_args=True, args='"argument with spaces"')
        command = self.worker(config)._build_command(script, config)
        subprocess.run(command, cwd=folder, check=True, timeout=10, capture_output=True)
        self.assertEqual((folder / "result.txt").read_text().strip(), "argument with spaces")

    def test_powershell_task_uses_file_mode(self):
        script = self.root / "run daily.ps1"
        config = task(script, use_args=True, args='"argument with spaces"')
        command = self.worker(config)._build_command(script, config)
        self.assertEqual(Path(command[0]).name, "powershell.exe")
        self.assertEqual(command[-3:], ["-File", str(script), "argument with spaces"])

    def test_parallel_exception_releases_queue_wait(self):
        config = task(self.root / "fake.exe")
        worker = self.worker(config)
        with patch.object(worker, "_run_one_task", side_effect=RuntimeError("fixture")):
            thread = Thread(target=worker._run_concurrent_group,
                            args=("fixture", [config, config], 1, 2), daemon=True)
            thread.start()
            thread.join(2)
            try:
                self.assertFalse(thread.is_alive())
                self.assertTrue(worker.had_task_error)
            finally:
                worker.stop_event.set()
                thread.join(2)

    def test_stats_count_each_execution_once_and_save_on_stop(self):
        config = task(self.root / "fake.exe")
        worker = self.worker(config)
        stats = worker.stats_manager
        stats.add_record(config.name, 10, "正常", "direct_process")
        first_path = stats.save_session()[0]
        stats.add_record(config.name, 20, "正常", "direct_process")
        with patch.object(worker, "_run_one_task", return_value="stop_requested"):
            worker.run()
        history = json.loads(stats.history_path.read_text(encoding="utf-8"))
        self.assertEqual(history[config.name]["count"], 2)
        self.assertEqual(history[config.name]["average_seconds"], 15)
        self.assertEqual(len(list(first_path.parent.glob("session_*.json"))), 2)

    def test_prepare_rebuilds_paths_and_allows_custom_scripts(self):
        bridge = JianszWebBridge(self.root)
        bridge._config.tasks = [task(self.root / "old" / "BetterGI.exe")]
        bridge.updates_discover(True)
        checked = []
        finished = Event()
        original_push = bridge._push_event

        def push(event, payload=None):
            original_push(event, payload)
            if event == "update_done":
                finished.set()

        def check(item, stop, log):
            checked.append(item.executable)
            if item.kind == "script":
                item.status = "已是最新"
            return item

        bridge._config.tasks = [
            task(self.root / "new" / "BetterGI.exe"),
            task(self.root / "another" / "BetterGI.exe"),
            task(self.root / "custom.py"),
        ]
        with patch.object(bridge, "_push_event", side_effect=push), \
             patch("ui.web_backend.check_item", side_effect=check):
            self.assertTrue(bridge.updates_start("prepare")["ok"])
            self.assertTrue(finished.wait(3))
        self.assertEqual(set(checked), {Path(t.script_path) for t in bridge._config.tasks})
        self.assertIsNotNone(bridge._prepared_update_ticket)

    def test_update_follows_full_command_override(self):
        configured = self.root / "BetterGI.exe"
        actual = self.root / "another" / "BetterGI.exe"
        config = task(configured, args=f'"{actual}" -flag', use_args=True)
        self.assertEqual(discover_items([config])[0].executable, actual)
        self.assertEqual(self.worker()._build_command(configured, config)[0], str(actual))

    def test_shutdown_issued_before_countdown_and_cancel_requires_success(self):
        bridge = JianszWebBridge(self.root)
        with patch("ui.web_backend.subprocess.run",
                   side_effect=[Mock(returncode=0), Mock(returncode=1), Mock(returncode=0)]) as run:
            bridge._on_worker_shutdown_prompt(30)
            self.assertEqual(run.call_args.args[0][1:], ["/s", "/t", "30"])
            self.assertTrue(bridge._shutdown_pending)
            self.assertFalse(bridge.cancel_shutdown()["ok"])
            self.assertTrue(bridge._shutdown_pending)
            self.assertTrue(bridge.cancel_shutdown()["ok"])
        self.assertFalse(bridge._shutdown_pending)

    def test_corrupt_zip_member_quarantines_completed_cache(self):
        fixture = self.root / "fixture.zip"
        with zipfile.ZipFile(fixture, "w") as archive:
            archive.writestr("OneDragon-Launcher.exe", b"synthetic-marker")
        corrupt = fixture.read_bytes().replace(b"synthetic-marker", b"corrupted-marker")
        item = UpdateItem("onedragon", "test", "script", self.root / "OneDragon-Launcher.exe", latest="v2.6.1")

        def download(url, destination, *args):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(corrupt)

        with patch("core.updates.download_file", side_effect=download):
            with self.assertRaisesRegex(UpdateError, "已隔离缓存"):
                _update_onedragon(item, Event(), lambda message: None)
        self.assertFalse(list(self.root.rglob("ZenlessZoneZero-OneDragon-Launcher.zip")))
