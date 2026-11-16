"""验证脚本检查范围、版本比较和更新中断后的恢复。"""
import json
import io
from pathlib import Path
import tempfile
from threading import Event
import unittest
import zipfile
from unittest.mock import patch, Mock
from types import SimpleNamespace
from urllib.error import HTTPError

from core import march7th_update
from core.updates import UpdateItem, UpdateError, UpdateCancelled, discover_items, check_item, update_item, _restore_onedragon, _update_onedragon
from ui.update_dialog import UpdateDialog


class Response(io.BytesIO):
    status = 200

    def __init__(self, payload):
        super().__init__(payload)
        self.headers = {"Content-Length": str(len(payload)), "ETag": '"synthetic"'}


class UpdateTests(unittest.TestCase):
    def test_march7th_403_uses_digest_for_exact_official_asset(self):
        expected = "a" * 64
        denied = HTTPError("https://api.github.com", 403, "Forbidden", {}, None)
        page = ('<clipboard-copy aria-label="Copy to clipboard digest for other.zip" '
                f'value="sha256:{"b" * 64}"></clipboard-copy>'
                '<clipboard-copy aria-label="Copy to clipboard digest for update.7z" '
                f'value="sha256:{expected}"></clipboard-copy>')
        with patch.object(march7th_update, "_request", side_effect=[denied, page]) as request:
            self.assertEqual(march7th_update._asset_digest("v2026.11.17", Event(), Mock()), expected)
        self.assertIn("/releases/expanded_assets/v2026.11.17", request.call_args.args[0])

    def test_march7th_download_failure_keeps_verified_local_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "m7a.exe"
            executable.touch()
            version_file = root / march7th_update.VERSION_FILE
            version_file.parent.mkdir(parents=True)
            version_file.write_text("v2026.11.15", "utf-8")
            item = UpdateItem("march7th", "三月七", "script", executable, latest="v2026.11.17", status="有更新")
            with patch("core.updates._assert_idle"), patch.object(
                march7th_update, "update", side_effect=march7th_update.UpdateDeferred("下载超时")
            ):
                update_item(item, Event(), Mock())
            self.assertEqual(item.status, "沿用本地版本")
            self.assertEqual(item.current, "v2026.11.15")
            self.assertIn("下载超时", item.detail)

    def test_march7th_failure_during_replacement_restores_old_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            version = march7th_update.VERSION_FILE
            stage = root / ".jiansz-march7th-123"
            package = stage / "update"
            for target, value in ((root, "v2026.11.15"), (package, "v2026.11.17")):
                (target / version).parent.mkdir(parents=True)
                (target / version).write_text(value, "utf-8")
                (target / "m7a.exe").write_text(value, "utf-8")
            real_replace = march7th_update.os.replace

            def fail_new_version(source, destination):
                if source == package / version:
                    raise PermissionError("file locked")
                real_replace(source, destination)

            with patch.object(march7th_update.os, "replace", side_effect=fail_new_version):
                with self.assertRaises(march7th_update.UpdateDeferred):
                    march7th_update._install(root, stage, "v2026.11.17", Event(), Mock(), None)
            self.assertEqual((root / version).read_text("utf-8"), "v2026.11.15")
            self.assertEqual((root / "m7a.exe").read_text("utf-8"), "v2026.11.15")
            self.assertFalse((root / march7th_update.JOURNAL).exists())

    def test_native_updater_progress_is_read_before_version_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            executable=root/"BetterGI.exe";executable.touch()
            (root/"BetterGI.update.exe").touch()
            item=UpdateItem("bettergi","BetterGI","script",executable,latest="0.67.0",status="有更新")
            def start(command,**kwargs):
                kwargs["stdout"].write(b"Downloading 42%\r")
                kwargs["stdout"].flush()
                return Mock(poll=Mock(return_value=0))
            clock=[0]
            def tick():clock[0]+=3;return clock[0]
            events=[]
            with patch("core.updates._assert_idle"),patch("core.updates.native_popen",side_effect=start), \
                 patch("core.updates.local_version",return_value="0.67.0"), \
                 patch("core.updates.time.monotonic",side_effect=tick), \
                 patch.dict("os.environ",{"LOCALAPPDATA":str(root)}):
                update_item(item,Mock(wait=Mock(return_value=False),is_set=Mock(return_value=False)),lambda _:None,progress=events.append)
            self.assertTrue(any(e["phase"]=="原工具更新" and e["percent"]==42 for e in events))
            self.assertEqual(events[-1]["phase"],"更新完成")
            self.assertEqual(item.status,"已是最新")

    def test_cancelled_download_does_not_fall_back_to_another_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            item=UpdateItem("onedragon","一条龙","script",root/"OneDragon-RuntimeLauncher.exe",latest="v2.6.1")
            with patch("core.updates.download_file",side_effect=UpdateCancelled("cancel")) as download:
                with self.assertRaises(UpdateCancelled):_update_onedragon(item,Event(),lambda _:None)
            self.assertEqual(download.call_count,1)

    def test_equal_file_version_does_not_update(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "BetterGI.exe"
            executable.touch()
            item = UpdateItem("bettergi", "BetterGI", "script", executable, "owner/repo")
            with patch("core.updates.local_version", return_value="0.67.0.0"), \
                 patch("core.updates._release_version", return_value="0.67.0"):
                check_item(item, Event(), lambda _: None)
            self.assertEqual(item.status, "已是最新")

    def test_local_newer_does_not_downgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "ok-ww.exe"
            executable.touch()
            item = UpdateItem("bettergi", "BetterGI", "script", executable, "owner/repo")
            with patch("core.updates.local_version", return_value="0.68.0"), \
                 patch("core.updates._release_version", return_value="0.67.0"):
                check_item(item, Event(), lambda _: None)
            self.assertEqual(item.status, "本地版本较新")

    def test_network_failure_is_not_up_to_date(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "BetterGI.exe"
            executable.touch()
            item = UpdateItem("bettergi", "BetterGI", "script", executable, "owner/repo")
            with patch("core.updates.local_version", return_value="0.66.0"), \
                 patch("core.updates._release_version", side_effect=OSError("offline")):
                check_item(item, Event(), lambda _: None)
            self.assertEqual(item.status, "检查失败")

    def test_discovery_contains_only_enabled_scripts(self):
        tasks = [SimpleNamespace(enabled=True, script_path=f"C:/Tools/{name}", name=name)
                 for name in ("ok-ww.exe", "March7th Assistant.exe", "OneDragon-RuntimeLauncher.exe", "BetterGI.exe")]
        tasks.append(SimpleNamespace(enabled=False, script_path="C:/Tools/MAA.exe", name="MAA"))
        items = discover_items(tasks)
        self.assertEqual(len(items), 4)
        self.assertEqual({item.kind for item in items}, {"script"})

    def test_pre_task_check_updates_before_reporting_ready(self):
        item = UpdateItem("bettergi", "BetterGI", "script", Path("C:/Tools/BetterGI.exe"), status="有更新")
        worker = SimpleNamespace(stop=Event(), log_message=Mock(), item_changed=Mock(), work_finished=Mock())
        def finish_update(*args, **kwargs):
            item.status = "已是最新"
            return item
        with patch("ui.update_dialog.check_item", return_value=item), \
             patch("ui.update_dialog.update_item", side_effect=finish_update) as update:
            UpdateDialog._work(worker, "prepare", [(0, item)])
        update.assert_called_once()
        worker.work_finished.emit.assert_called_once_with(True)

    def test_update_failure_prevents_automatic_launch(self):
        item = UpdateItem("bettergi", "BetterGI", "script", Path("C:/Tools/BetterGI.exe"), status="有更新")
        worker = SimpleNamespace(stop=Event(), log_message=Mock(), item_changed=Mock(), work_finished=Mock())
        with patch("ui.update_dialog.check_item", return_value=item), \
             patch("ui.update_dialog.update_item", side_effect=UpdateError("download failed")):
            UpdateDialog._work(worker, "prepare", [(0, item)])
        self.assertEqual(item.status, "更新失败")
        worker.work_finished.emit.assert_called_once_with(False)

    def test_check_failure_prevents_automatic_launch(self):
        item = UpdateItem("bettergi", "BetterGI", "script", Path("C:/Tools/BetterGI.exe"), status="检查失败")
        worker = SimpleNamespace(stop=Event(), log_message=Mock(), item_changed=Mock(), work_finished=Mock())
        with patch("ui.update_dialog.check_item", return_value=item), patch("ui.update_dialog.update_item") as update:
            UpdateDialog._work(worker, "prepare", [(0, item)])
        update.assert_not_called()
        worker.work_finished.emit.assert_called_once_with(False)

    def test_current_script_is_ready_without_updating(self):
        item = UpdateItem("bettergi", "BetterGI", "script", Path("C:/Tools/BetterGI.exe"), status="已是最新")
        worker = SimpleNamespace(stop=Event(), log_message=Mock(), item_changed=Mock(), work_finished=Mock())
        with patch("ui.update_dialog.check_item", return_value=item), patch("ui.update_dialog.update_item") as update:
            UpdateDialog._work(worker, "prepare", [(0, item)])
        update.assert_not_called()
        worker.work_finished.emit.assert_called_once_with(True)

    def test_partial_runtime_replacement_rolls_back_both_components(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / ".jiansz-backup-123"
            backup.mkdir()
            (backup / "OneDragon-RuntimeLauncher.exe").write_bytes(b"old exe")
            (backup / ".runtime").mkdir()
            (backup / ".runtime/old.dll").write_bytes(b"old dll")
            (root / "OneDragon-RuntimeLauncher.exe").write_bytes(b"new exe")
            config = root / "config.json"
            config.write_text('{"my_data": true}')
            journal = root / ".jiansz-update.json"
            journal.write_text(json.dumps({"backup": backup.name,
                                          "names": ["OneDragon-RuntimeLauncher.exe", ".runtime"]}))
            _restore_onedragon(root, journal)
            self.assertEqual((root / "OneDragon-RuntimeLauncher.exe").read_bytes(), b"old exe")
            self.assertEqual((root / ".runtime/old.dll").read_bytes(), b"old dll")
            self.assertEqual(config.read_text(), '{"my_data": true}')
            self.assertFalse(journal.exists())

    def test_runtime_package_preserves_user_files_and_backs_up_previous_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "OneDragon-RuntimeLauncher.exe"
            executable.write_bytes(b"old exe")
            (root / ".runtime").mkdir()
            (root / ".runtime/python311.dll").write_bytes(b"old runtime")
            config = root / "config.yml"
            config.write_text("my_setting: true")
            payload = io.BytesIO()
            with zipfile.ZipFile(payload, "w") as package:
                package.writestr(executable.name, b"new exe")
                package.writestr(".runtime/python312.dll", b"new runtime")
                package.writestr("config.yml", b"overwrite user settings")
            payload.seek(0)
            item = UpdateItem("onedragon", "一条龙", "script", executable, "owner/repo", latest="v2.6.1")
            def version_output(command, cwd):
                self.assertTrue((cwd / "src").is_dir())
                return "launcher v2.6.1"
            with patch("core.update_download.urlopen", return_value=Response(payload.getvalue())), \
                 patch("core.updates.run_output", side_effect=version_output), \
                 patch("core.updates.local_version", return_value="v2.6.1"), \
                 patch("core.updates._assert_idle"):
                _update_onedragon(item, Event(), lambda _: None)
            self.assertEqual(config.read_text(), "my_setting: true")
            self.assertEqual(executable.read_bytes(), b"new exe")
            self.assertEqual((root / ".runtime/python312.dll").read_bytes(), b"new runtime")
            backup = next(root.glob(".jiansz-backup-*"))
            self.assertEqual((backup / executable.name).read_bytes(), b"old exe")
            self.assertFalse((root / ".jiansz-update.json").exists())

    def test_runtime_package_rejects_parent_paths_before_replacing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "OneDragon-RuntimeLauncher.exe"
            executable.write_bytes(b"old exe")
            payload = io.BytesIO()
            with zipfile.ZipFile(payload, "w") as package:
                package.writestr("../escaped.exe", b"unexpected")
            payload.seek(0)
            item = UpdateItem("onedragon", "一条龙", "script", executable, "owner/repo", latest="v2.6.1")
            with patch("core.update_download.urlopen", return_value=Response(payload.getvalue())), self.assertRaisesRegex(UpdateError, "无效路径"):
                _update_onedragon(item, Event(), lambda _: None)
            self.assertEqual(executable.read_bytes(), b"old exe")


if __name__ == "__main__":
    unittest.main()
