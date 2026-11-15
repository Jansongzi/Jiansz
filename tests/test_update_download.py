"""下载中断、续传校验、取消和真实进度的回归测试。"""
import io
import json
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import Mock, patch

from core.update_download import download_file, UpdateError, UpdateCancelled


class Response(io.BytesIO):
    def __init__(self, payload, *, status=200, headers=None, fail_after=None, on_read=None):
        super().__init__(payload)
        self.status = status
        self.headers = headers if headers is not None else {"Content-Length": str(len(payload)), "ETag": '"v1"'}
        self.fail_after = fail_after
        self.on_read = on_read

    def read1(self, amount=-1):
        if self.fail_after is not None and self.tell() >= self.fail_after:
            raise TimeoutError("synthetic timeout")
        result = super().read(min(4, amount))
        if self.on_read:
            self.on_read()
        return result


def stop_token():
    return Mock(is_set=Mock(return_value=False), wait=Mock(return_value=False))


class DownloadTests(unittest.TestCase):
    def test_timeout_resumes_from_last_written_byte_and_reports_actual_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "package.zip"
            progress, logs = [], []
            responses = [Response(b"abcdefgh", fail_after=4),
                         Response(b"efgh", status=206, headers={"Content-Length":"4","Content-Range":"bytes 4-7/8","ETag":'"v1"'})]
            with patch("core.update_download.urlopen", side_effect=responses) as opened:
                download_file("https://example.org/package.zip", target, stop_token(), logs.append, progress.append)
            self.assertEqual(target.read_bytes(), b"abcdefgh")
            self.assertEqual(opened.call_args_list[1].args[0].get_header("Range"), "bytes=4-")
            self.assertEqual(opened.call_args_list[1].args[0].get_header("If-range"), '"v1"')
            self.assertTrue(any(p["phase"] == "等待重试" and p["completed"] == 4 for p in progress))
            self.assertEqual(progress[-1]["percent"], 100)

    def test_retry_after_application_restart_reuses_matching_partial_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "package.zip"
            with patch("core.update_download.urlopen", return_value=Response(b"abcdefgh", fail_after=4)):
                with self.assertRaises(UpdateError):
                    download_file("https://example.org/package.zip", target, stop_token(), lambda _:None, attempts=1)
            self.assertFalse(target.exists())
            self.assertEqual(target.with_name(target.name+".part").read_bytes(), b"abcd")
            response=Response(b"efgh", status=206,headers={"Content-Range":"bytes 4-7/8","ETag":'"v1"'})
            with patch("core.update_download.urlopen", return_value=response) as opened:
                download_file("https://example.org/package.zip", target, stop_token(), lambda _:None)
            self.assertEqual(opened.call_args.args[0].get_header("Range"), "bytes=4-")
            self.assertEqual(target.read_bytes(), b"abcdefgh")

    def test_server_ignoring_range_restarts_instead_of_appending(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "package.zip"
            with patch("core.update_download.urlopen", side_effect=[Response(b"abcdefgh",fail_after=4),Response(b"new-file")]):
                download_file("https://example.org/package.zip",target,stop_token(),lambda _:None)
            self.assertEqual(target.read_bytes(), b"new-file")

    def test_changed_etag_restarts_instead_of_mixing_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "package.zip"
            changed=Response(b"zzzz",status=206,headers={"Content-Range":"bytes 4-7/8","ETag":'"v2"'})
            with patch("core.update_download.urlopen", side_effect=[Response(b"abcdefgh",fail_after=4),changed,
                                                                     Response(b"new-file",headers={"Content-Length":"8","ETag":'"v2"'})]):
                download_file("https://example.org/package.zip",target,stop_token(),lambda _:None)
            self.assertEqual(target.read_bytes(), b"new-file")

    def test_wrong_range_is_rejected_without_completing_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/"package.zip"
            response=Response(b"bad",status=206,headers={"Content-Range":"bytes 4-6/7","ETag":'"v1"'})
            with patch("core.update_download.urlopen", return_value=response):
                with self.assertRaisesRegex(UpdateError,"续传范围"):
                    download_file("https://example.org/file",target,stop_token(),lambda _:None)
            self.assertFalse(target.exists())

    def test_short_read_is_not_marked_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/"package.zip"
            response=Response(b"abcd",headers={"Content-Length":"8","ETag":'"v1"'})
            with patch("core.update_download.urlopen",return_value=response):
                with self.assertRaises(UpdateError):
                    download_file("https://example.org/file",target,stop_token(),lambda _:None,attempts=1)
            self.assertFalse(target.exists())

    def test_unknown_length_does_not_invent_percent(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/"package.zip";events=[]
            with patch("core.update_download.urlopen",return_value=Response(b"abcd",headers={} )):
                download_file("https://example.org/file",target,stop_token(),lambda _:None,events.append)
            self.assertTrue(any(e["phase"]=="下载更新包" and e["percent"] is None for e in events))
            self.assertEqual(events[-1]["completed"],4)

    def test_cancel_keeps_partial_data_and_is_not_a_network_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/"package.zip";stop=Event()
            with patch("core.update_download.urlopen",return_value=Response(b"abcdefgh",on_read=stop.set)) as opened:
                with self.assertRaises(UpdateCancelled):
                    download_file("https://example.org/file",target,stop,lambda _:None)
            self.assertEqual(opened.call_count,1)
            self.assertEqual(target.with_name(target.name+".part").read_bytes(),b"abcd")
            self.assertFalse(target.exists())

    def test_cancel_before_download_does_not_connect(self):
        with tempfile.TemporaryDirectory() as tmp:
            stop=Event();stop.set()
            with patch("core.update_download.urlopen") as opened, self.assertRaises(UpdateCancelled):
                download_file("https://example.org/file",Path(tmp)/"package.zip",stop,lambda _:None)
            opened.assert_not_called()

    def test_complete_matching_cache_is_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/"package.zip"
            with patch("core.update_download.urlopen",return_value=Response(b"abcdefgh")):
                download_file("https://example.org/file",target,stop_token(),lambda _:None)
            with patch("core.update_download.urlopen") as opened:
                download_file("https://example.org/file",target,stop_token(),lambda _:None)
            opened.assert_not_called()
