"""更新包下载：显示真实流量，连接中断后按服务端校验信息续传。"""
from __future__ import annotations

from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class UpdateError(RuntimeError):
    pass


class UpdateCancelled(UpdateError):
    pass


def report_progress(callback, phase: str, *, completed=0, total=None, speed=0, unit="steps", message=""):
    event = {"phase": phase, "completed": completed, "total": total, "speed": speed,
             "unit": unit, "message": message,
             "percent": min(100, completed * 100 / total) if total and total > 0 else None}
    if callback:
        callback(event)
    return event


def _check_stop(stop):
    if stop.is_set():
        raise UpdateCancelled("已取消更新，已下载部分保留，下次可继续。")


def _save_metadata(path: Path, data: dict):
    pending = path.with_name(path.name + ".tmp")
    with pending.open("w", encoding="utf-8") as output:
        json.dump(data, output)
        output.flush()
        os.fsync(output.fileno())
    pending.replace(path)


def download_file(url: str, destination: Path, stop, log, progress=None,
                  *, attempts: int = 3, timeout: int = 30, maximum: int = 2 * 1024 ** 3) -> Path:
    """只有匹配 URL 和服务端校验值才续传；超时保留已完成的字节。"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    metadata_path = destination.with_name(destination.name + ".json")
    try:
        metadata = json.loads(metadata_path.read_text("utf-8"))
        if not isinstance(metadata, dict) or metadata.get("url") != url:
            metadata = {}
    except (OSError, ValueError):
        metadata = {}
    if (metadata.get("complete") is True and destination.is_file()
            and destination.stat().st_size == metadata.get("total")):
        _check_stop(stop)
        log("复用已完整下载的更新包，接下来校验文件。")
        report_progress(progress, "下载完成", completed=metadata["total"], total=metadata["total"], unit="bytes")
        return destination
    for attempt in range(1, attempts + 1):
        _check_stop(stop)
        validator = metadata.get("validator")
        offset = partial.stat().st_size if partial.is_file() and validator else 0
        expected = metadata.get("total")
        if offset and expected and offset == expected:
            partial.replace(destination)
            metadata["complete"] = True
            _save_metadata(metadata_path, metadata)
            return destination
        headers = {"User-Agent": "Jiansz-update/0.1.1", "Accept-Encoding": "identity"}
        if offset:
            headers.update({"Range": f"bytes={offset}-", "If-Range": validator})
        report_progress(progress, "连接下载源", completed=offset, total=expected, unit="bytes",
                        message=f"第 {attempt}/{attempts} 次连接" + ("，准备续传" if offset else ""))
        try:
            with urlopen(Request(url, headers=headers), timeout=timeout) as response:
                status = getattr(response, "status", 200)
                etag = response.headers.get("ETag", "")
                current_validator = etag if etag and not etag.startswith("W/") else response.headers.get("Last-Modified", "")
                length = response.headers.get("Content-Length", "")
                content_length = int(length) if length.isdecimal() else None
                if status == 206:
                    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                    if not match or int(match[1]) != offset:
                        raise UpdateError("下载源返回的续传范围无效，请稍后重试。")
                    total = int(match[3])
                    if int(match[2]) < offset or int(match[2]) >= total:
                        raise UpdateError("下载源返回的文件范围无效。")
                    if offset and (not validator or current_validator != validator or (expected and total != expected)):
                        metadata = {}
                        raise URLError("更新包已变化，重新连接")
                elif status == 200:
                    # 服务端未接受 Range 或资源已变化，覆盖部分文件重新下载。
                    offset, total = 0, content_length
                else:
                    raise UpdateError("下载源返回了无法处理的响应。")
                if total is not None and (total <= 0 or total > maximum):
                    raise UpdateError("启动器更新包大小异常。")
                metadata = {"url": url, "validator": current_validator, "total": total, "complete": False}
                _save_metadata(metadata_path, metadata)
                transferred = offset
                started = last_emit = last_log = time.monotonic()
                start_offset = offset
                read = getattr(response, "read1", response.read)
                report_progress(progress, "下载更新包", completed=transferred, total=total, unit="bytes")
                with partial.open("ab" if offset else "wb") as output:
                    while True:
                        _check_stop(stop)
                        try:
                            chunk = read(64 * 1024)
                        except OSError:
                            raise URLError("下载连接中断") from None
                        if not chunk:
                            break
                        output.write(chunk)
                        transferred += len(chunk)
                        if transferred > maximum or (total is not None and transferred > total):
                            raise UpdateError("下载数据超过预期大小，已停止更新。")
                        now = time.monotonic()
                        speed = (transferred - start_offset) / max(.01, now - started)
                        if now - last_emit >= .25:
                            report_progress(progress, "下载更新包", completed=transferred, total=total, speed=speed, unit="bytes")
                            last_emit = now
                        if now - last_log >= 8:
                            size_text = f" / {total / 1024**2:.1f} MB" if total else ""
                            log(f"已下载 {transferred / 1024**2:.1f} MB{size_text}，{speed / 1024**2:.2f} MB/s。")
                            last_log = now
                    output.flush()
                    os.fsync(output.fileno())
                _check_stop(stop)
                if total is not None and transferred != total:
                    raise URLError("连接提前结束")
                if not transferred:
                    raise URLError("空响应")
                partial.replace(destination)
                metadata.update(total=transferred, complete=True)
                _save_metadata(metadata_path, metadata)
                report_progress(progress, "下载完成", completed=transferred, total=transferred, unit="bytes")
                return destination
        except UpdateCancelled:
            raise
        except HTTPError as exc:
            exc.close()
            if exc.code == 416:
                metadata = {}
            elif exc.code not in {408, 429, 500, 502, 503, 504}:
                raise UpdateError(f"下载源返回 HTTP {exc.code}，正在尝试其他官方源。") from None
        except (URLError, TimeoutError, ConnectionError, HTTPException):
            pass
        _check_stop(stop)
        if attempt < attempts:
            size = partial.stat().st_size if partial.exists() else 0
            message = f"连接中断，已保留 {size / 1024**2:.1f} MB，{attempt * 2} 秒后自动重试。"
            log(message)
            report_progress(progress, "等待重试", completed=size, total=metadata.get("total"), unit="bytes", message=message)
            if stop.wait(attempt * 2):
                _check_stop(stop)
    raise UpdateError("下载连接多次超时或中断，已保留下载进度。请检查网络后重试。")
