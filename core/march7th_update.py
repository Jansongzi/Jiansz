"""三月七官方增量包的获取、校验和可恢复安装。"""
from contextlib import contextmanager
from html import unescape
from http.client import HTTPException
import hashlib
import json
import msvcrt
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import time
from urllib.parse import quote
from urllib.request import Request, urlopen

from packaging.version import Version

from core.native_process import native_popen
from core.update_download import UpdateCancelled, UpdateError, download_file, report_progress


REPOSITORY = "moesnow/March7thAssistant"
PACKAGE = "update.7z"
VERSION_FILE = Path("assets/config/version.txt")
JOURNAL = ".jiansz-march7th-update.json"
ROOT_FILES = {
    "m7a.exe", "March7th Assistant.exe", "March7th Launcher.exe",
    "March7th Updater.exe", "README.md", "AGENTS.md",
}
NETWORK_ERRORS = (OSError, ValueError, HTTPException)


class UpdateDeferred(UpdateError):
    """更新在替换前失败，或已回滚，可以核对后继续使用本地版本。"""


@contextmanager
def install_lock(root):
    # Windows 会在进程异常结束时释放锁，下次运行便可自动恢复。
    with (root / ".jiansz-march7th.lock").open("a+b") as handle:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise UpdateError("另一个 Jiansz 正在处理三月七更新。") from exc
        yield


def _request(url, accept):
    headers = {"User-Agent": "Jiansz-update", "Accept": accept}
    with urlopen(Request(url, headers=headers), timeout=10) as response:
        # Release 元数据和网页都应远小于此上限。
        return response.read(2 * 1024 * 1024).decode("utf-8")


def _asset_digest(tag, stop, log):
    tag = quote(tag, safe="")
    try:
        data = json.loads(_request(f"https://api.github.com/repos/{REPOSITORY}/releases/tags/{tag}",
                                   "application/vnd.github+json"))
        asset = next(asset for asset in data["assets"] if asset["name"] == PACKAGE)
        digest = re.fullmatch(r"sha256:([a-f0-9]{64})", asset.get("digest") or "")
        if digest:
            return digest[1]
        reason = "接口未提供增量包校验值"
    except (OSError, ValueError, HTTPException, KeyError, StopIteration, TypeError) as exc:
        reason = f"HTTP {exc.code}" if hasattr(exc, "code") else type(exc).__name__
    log(f"三月七 GitHub API 查询失败（{reason}），自动改用官方发布网页。")
    _check_stop(stop)
    try:
        page = _request(f"https://github.com/{REPOSITORY}/releases/expanded_assets/{tag}", "text/html")
        # 摘要复制按钮包含对应的文件名，不能取相邻附件的 SHA-256。
        for button in re.findall(r"<clipboard-copy\b[^>]*>", page):
            label = re.search(r'\baria-label="([^"]+)"', button)
            digest = re.search(r'\bvalue="sha256:([a-f0-9]{64})"', button)
            if label and digest and unescape(label[1]) == f"Copy to clipboard digest for {PACKAGE}":
                return digest[1]
    except NETWORK_ERRORS as exc:
        reason = f"HTTP {exc.code}" if hasattr(exc, "code") else type(exc).__name__
        raise UpdateDeferred(f"官方发布网页查询失败（{reason}）。") from exc
    raise UpdateDeferred("官方发布网页未提供增量包的校验值。")


def _check_stop(stop):
    if stop.is_set():
        raise UpdateCancelled("已停止三月七更新。")


def _checked_relative(name):
    relative = Path(name)
    if (not relative.parts or relative.is_absolute() or relative.drive
            or any(part in {".", ".."} or part.rstrip(" .") != part or ":" in part
                   or Path(part).is_reserved() for part in relative.parts)
            or not (len(relative.parts) == 1 and relative.name in ROOT_FILES
                    or len(relative.parts) > 1 and relative.parts[0] in {"assets", "libraries"})):
        raise UpdateError("三月七更新包或恢复记录中含有非程序文件。")
    return relative


def _ensure_regular(root, relative):
    path = root
    for part in relative.parts:
        path /= part
        try:
            attrs = path.lstat().st_file_attributes
        except FileNotFoundError:
            continue
        if attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise UpdateError("更新路径包含链接或重解析点。")


def restore(root):
    journal = root / JOURNAL
    if not journal.exists():
        return
    data = json.loads(journal.read_text("utf-8"))
    stage_name = data["stage"]
    if not re.fullmatch(r"\.jiansz-march7th-\d+", stage_name):
        raise UpdateError("三月七更新恢复记录无效。")
    stage = root / stage_name
    _ensure_regular(root, Path(stage_name))
    if not (stage / "update").is_dir() or not (stage / "backup").is_dir():
        raise UpdateError("三月七更新的备份或暂存目录缺失。")
    for name, existed in reversed(data["files"]):
        relative = _checked_relative(name)
        _ensure_regular(root, relative)
        _ensure_regular(stage, Path("backup") / relative)
        live = root / relative
        old = stage / "backup" / relative
        if old.is_file():
            os.replace(old, live)
        elif not existed:
            live.unlink(missing_ok=True)
    journal.unlink()
    shutil.rmtree(stage, ignore_errors=True)


def _verify(archive, digest, stop):
    checksum = hashlib.sha256()
    with archive.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            _check_stop(stop)
            checksum.update(block)
    return checksum.hexdigest() == digest


def _download(url, archive, stop, log, progress, deadline):
    try:
        download_file(url, archive, stop, log, progress, timeout=12,
                      deadline=min(deadline, time.monotonic() + 100))
        return
    except UpdateCancelled:
        raise
    except (UpdateError, HTTPException) as exc:
        log(f"三月七下载失败：{exc} 自动改用 Windows curl 下载同一个官方包。")
    _check_stop(stop)
    remaining = int(deadline - time.monotonic())
    if remaining <= 0:
        raise UpdateDeferred("已达到下载总时限。")
    curl = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/curl.exe"
    partial = archive.with_suffix(".curl.part")
    report_progress(progress, "替补下载", message="通过 Windows curl 下载官方增量包")
    command = [str(curl), "--fail", "--silent", "--location", "--proto", "=https",
               "--proto-redir", "=https", "--connect-timeout", "12", "--max-time",
               str(remaining), "--max-filesize", str(2 * 1024 ** 3),
               "--output", str(partial), url]
    with native_popen(command, cwd=archive.parent, stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW) as process:
        while process.poll() is None:
            if stop.wait(.5) or time.monotonic() >= deadline:
                process.kill()
                _check_stop(stop)
                raise UpdateDeferred("替补下载达到总时限。")
            if partial.exists():
                report_progress(progress, "替补下载", completed=partial.stat().st_size, unit="bytes")
        if process.returncode:
            raise UpdateDeferred(f"替补下载失败（curl 退出码 {process.returncode}）。")
    partial.replace(archive)


def _extract(archive, stage, deadline, stop):
    tar = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/tar.exe"
    # Windows 自带的 bsdtar 默认拒绝绝对路径、上级目录和穿过符号链接的写入。
    with native_popen([str(tar), "-xf", str(archive), "-C", str(stage)], cwd=stage,
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                      creationflags=subprocess.CREATE_NO_WINDOW) as process:
        while process.poll() is None:
            if stop.wait(.25) or time.monotonic() >= deadline:
                process.kill()
                _check_stop(stop)
                raise UpdateDeferred("解压超过时限。")
        if process.returncode:
            raise UpdateDeferred(f"解压官方更新包失败（退出码 {process.returncode}）。")


def _install(root, stage, target, stop, assert_idle, progress):
    unpacked = stage / "update"
    _ensure_regular(stage, Path("update"))
    files = []
    for path in unpacked.rglob("*"):
        relative = path.relative_to(unpacked)
        _ensure_regular(unpacked, relative)
        if path.is_dir():
            continue
        relative = _checked_relative(str(relative))
        _ensure_regular(root, relative)
        live = root / relative
        if live.exists() and not live.is_file():
            raise UpdateError(f"三月七更新文件与现有目录冲突：{relative}")
        files.append((relative, live.is_file()))
    if not (unpacked / "m7a.exe").is_file():
        raise UpdateError("官方增量包缺少三月七主程序。")
    if (unpacked / VERSION_FILE).read_text("utf-8-sig").strip() != target:
        raise UpdateError("更新包版本与目标版本不一致。")
    files.sort(key=lambda entry: entry[0] == VERSION_FILE)
    assert_idle(root)
    _check_stop(stop)
    backup = stage / "backup"
    backup.mkdir()
    journal = root / JOURNAL
    pending = stage / "journal.json"
    with pending.open("w", encoding="utf-8") as stream:
        json.dump({"stage": stage.name, "files": [(str(p), exists) for p, exists in files]}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    pending.replace(journal)
    try:
        for index, (relative, existed) in enumerate(files, 1):
            _check_stop(stop)
            live = root / relative
            live.parent.mkdir(parents=True, exist_ok=True)
            if existed:
                old = backup / relative
                old.parent.mkdir(parents=True, exist_ok=True)
                os.replace(live, old)
            os.replace(unpacked / relative, live)
            if index % 50 == 0 or index == len(files):
                report_progress(progress, "安装三月七", completed=index, total=len(files))
        journal.unlink()
    except Exception as exc:
        restore(root)
        if isinstance(exc, UpdateCancelled):
            raise
        raise UpdateDeferred(f"安装失败，已还原旧版：{exc}") from exc


def update(root, tag, stop, log, progress, assert_idle, *, timeout):
    deadline = time.monotonic() + min(timeout, 480)
    with install_lock(root):
        assert_idle(root)
        restore(root)
        _check_stop(stop)
        installed = (root / VERSION_FILE).read_text("utf-8-sig").strip()
        if Version(installed) >= Version(tag):
            return
        report_progress(progress, "获取官方更新包", message="检查 GitHub 发布附件和校验值")
        digest = _asset_digest(tag, stop, log)
        url = f"https://github.com/{REPOSITORY}/releases/download/{quote(tag, safe='')}/{PACKAGE}"
        cache = Path(os.environ.get("LOCALAPPDATA", str(root))) / "Jiansz/march7th-downloads" / digest
        archive = cache / PACKAGE
        stage = None
        extracted = False
        try:
            if not archive.is_file() or not _verify(archive, digest, stop):
                archive.unlink(missing_ok=True)
                _download(url, archive, stop, log, progress, deadline)
                if not _verify(archive, digest, stop):
                    archive.unlink()
                    raise UpdateDeferred("下载包 SHA-256 校验失败。")
            log(f"三月七官方增量包 {tag} 的 SHA-256 已校验。")
            _check_stop(stop)
            stage = root / f".jiansz-march7th-{time.time_ns()}"
            stage.mkdir()
            report_progress(progress, "解压三月七", message="正在解压已校验的官方增量包")
            _extract(archive, stage, deadline, stop)
            extracted = True
        except (UpdateCancelled, UpdateDeferred):
            raise
        except (OSError, UpdateError, HTTPException) as exc:
            raise UpdateDeferred(f"下载或解压失败：{exc}") from exc
        finally:
            # 解压成功后交由安装及恢复逻辑处理，失败时不留下暂存目录。
            if stage is not None and not extracted:
                shutil.rmtree(stage, ignore_errors=True)
        try:
            _install(root, stage, tag, stop, assert_idle, progress)
        finally:
            if not (root / JOURNAL).exists():
                shutil.rmtree(stage, ignore_errors=True)
