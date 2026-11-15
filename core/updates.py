"""已配置脚本的版本检查和手动更新入口。"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import json
import hashlib
import os
from pathlib import Path
import re
import subprocess
import time
import zipfile
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from packaging.version import Version, InvalidVersion
import psutil
from core.native_process import native_popen
from core.core import resolve_task_command
from core.update_download import UpdateError, UpdateCancelled, download_file, report_progress


@dataclass
class UpdateItem:
    key: str
    name: str
    kind: str
    executable: Path
    repository: str = ""
    current: str = ""
    latest: str = ""
    status: str = "待检查"
    detail: str = ""
    progress: dict | None = None


SCRIPT_INFO = {
    "ok-ww.exe": ("okww", "OK-WW", "ok-oldking/ok-ww-update"),
    "march7th assistant.exe": ("march7th", "三月七小助手", "moesnow/March7thAssistant"),
    "march7th launcher.exe": ("march7th", "三月七小助手", "moesnow/March7thAssistant"),
    "m7a.exe": ("march7th", "三月七小助手", "moesnow/March7thAssistant"),
    "onedragon-runtimelauncher.exe": ("onedragon", "绝区零一条龙", "OneDragon-Anything/ZenlessZoneZero-OneDragon"),
    "onedragon-launcher.exe": ("onedragon", "绝区零一条龙", "OneDragon-Anything/ZenlessZoneZero-OneDragon"),
    "bettergi.exe": ("bettergi", "BetterGI", "babalae/better-genshin-impact"),
}


def run_output(command: list[str], cwd: Path, timeout: int = 30) -> str:
    with native_popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                      creationflags=subprocess.CREATE_NO_WINDOW) as process:
        try:
            output, _ = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise UpdateError("版本查询超时，请检查网络后重试。")
    if process.returncode:
        raise UpdateError("版本查询命令失败，请检查工具日志或网络连接。")
    return output.decode("utf-8-sig", errors="replace").strip()


def discover_items(tasks) -> list[UpdateItem]:
    result = []
    seen = set()
    for task in tasks:
        if not task.enabled:
            continue
        try:
            path, _ = resolve_task_command(task)
        except (ValueError, OSError) as exc:
            result.append(UpdateItem("invalid", task.name, "invalid", Path(task.script_path),
                                     status="检查失败", detail=f"无法解析启动命令：{exc}"))
            continue
        info = SCRIPT_INFO.get(path.name.lower())
        if info is None:
            result.append(UpdateItem(f"unknown:{path}", task.name, "unsupported", path,
                                     status="尚未适配", detail="当前支持 OK-WW、三月七、绝区零一条龙和 BetterGI。"))
            continue
        key, name, repository = info
        identity = (key, os.path.normcase(str(path.resolve())))
        if identity in seen:
            continue
        seen.add(identity)
        result.append(UpdateItem(key, name, "script", path, repository))
    return result


def file_version(path: Path) -> str:
    version = ctypes.windll.version
    version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    size = version.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return ""
    data = ctypes.create_string_buffer(size)
    version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    version.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                     ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
    if not version.GetFileVersionInfoW(str(path), 0, size, data):
        return ""
    value = ctypes.c_void_p()
    length = wintypes.UINT()
    if not version.VerQueryValueW(data, "\\", ctypes.byref(value), ctypes.byref(length)):
        return ""
    words = ctypes.cast(value, ctypes.POINTER(wintypes.DWORD))
    ms, ls = words[4], words[5]
    return f"{ms >> 16}.{ms & 0xffff}.{ls >> 16}.{ls & 0xffff}"


def local_version(item: UpdateItem) -> str:
    root = item.executable.parent
    if item.key == "okww":
        data = json.loads((root / "data/apps/ok-ww/app.json").read_text("utf-8-sig"))
        return data.get("current_version", "")
    if item.key == "march7th":
        return (root / "assets/config/version.txt").read_text("utf-8-sig").strip()
    if item.key == "onedragon":
        output = run_output([str(item.executable), "--version"], root)
        match = re.search(r"v\d+\.\d+\.\d+(?:-[\w.]+)?", output)
        return match.group() if match else ""
    return file_version(item.executable)


def _stable_version(value: str) -> Version:
    try:
        return Version(value.strip().removeprefix("v"))
    except InvalidVersion as exc:
        raise UpdateError(f"无法识别版本号 {value!r}。") from exc


def _release_version(repository: str) -> str:
    request = Request(f"https://github.com/{repository}/releases/latest",
                      headers={"User-Agent": "Jiansz-update-check/0.1.1"})
    for attempt in range(2):
        try:
            with urlopen(request, timeout=15) as response:
                final = urlparse(response.url)
            break
        except OSError:
            if attempt:
                raise UpdateError("连接上游发布页失败，已重试，请检查网络后再次检查。")
    prefix = f"/{repository}/releases/tag/"
    if final.hostname != "github.com" or not final.path.lower().startswith(prefix.lower()):
        raise UpdateError("上游发布地址未返回稳定版本。")
    tag = final.path[len(prefix):]
    parsed = _stable_version(tag)
    if parsed.is_prerelease or parsed.is_devrelease:
        raise UpdateError("上游最新发布仍是测试版本，请在原工具中选择更新通道。")
    return tag


def _remote_tags(remote: str) -> list[str]:
    request = Request(remote.rstrip("/") + "/info/refs?service=git-upload-pack",
                      headers={"User-Agent": "git/2.45.0", "Git-Protocol": "version=0"})
    with urlopen(request, timeout=20) as response:
        advertisement = response.read(10 * 1024 * 1024)
    tags = re.findall(rb"refs/tags/([^\x00\s]+)", advertisement)
    return [tag.decode("utf-8").removesuffix("^{}") for tag in tags]


def check_item(item: UpdateItem, stop, log) -> UpdateItem:
    try:
        if stop.is_set():
            raise UpdateError("已停止检查。")
        if item.kind == "invalid":
            item.status = "检查失败"
            log(f"{item.name}：{item.detail}")
            return item
        if item.kind == "unsupported":
            item.status = "尚未适配"
            return item
        item.latest = ""
        item.detail = ""
        item.progress = None
        if item.key == "onedragon" and (item.executable.parent / ".jiansz-update.json").exists():
            _assert_idle(item.executable.parent)
            _restore_onedragon(item.executable.parent, item.executable.parent / ".jiansz-update.json")
        if not item.executable.is_file():
            raise UpdateError("程序路径无效，请设置正确路径。")
        item.current = local_version(item)
        if item.key == "okww":
            data = json.loads((item.executable.parent / "data/apps/ok-ww/app.json").read_text("utf-8-sig"))
            profile = next(p for p in data["profiles"] if p["name"] == data["current_profile"])
            remote = profile["git_url"]
            parsed = urlparse(remote)
            if parsed.hostname not in {"github.com", "cnb.cool"}:
                raise UpdateError("当前 OK-WW 更新源尚未适配，请使用原启动器检查。")
            tags = _remote_tags(remote)
            stable = []
            for tag in tags:
                try:
                    version = _stable_version(tag)
                    if not version.is_prerelease and not version.is_devrelease:
                        stable.append((version, tag))
                except UpdateError:
                    continue
            if not stable:
                raise UpdateError("当前更新源没有返回可识别的稳定版本。")
            item.latest = max(stable)[1]
        else:
            item.latest = _release_version(item.repository)
        current = _stable_version(item.current)
        latest = _stable_version(item.latest)
        if current < latest:
            item.status = "有更新"
        elif current == latest:
            item.status = "已是最新"
        else:
            item.status = "本地版本较新"
        item.detail = ""
    except Exception as exc:
        item.status = "检查失败"
        item.detail = str(exc) if isinstance(exc, UpdateError) else "查询失败，请检查路径、网络和原工具日志。"
    log(f"{item.name}：{item.status}，本地 {item.current or '未知'}，上游 {item.latest or '未知'}。")
    return item


def _assert_idle(root: Path) -> None:
    for process in psutil.process_iter(["exe", "name"]):
        try:
            executable = process.info.get("exe")
            if executable and Path(executable).resolve().is_relative_to(root.resolve()):
                raise UpdateError("该工具仍在运行，请先退出该工具再更新。")
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            continue


def _restore_onedragon(root: Path, journal: Path) -> None:
    if not journal.exists():
        return
    data = json.loads(journal.read_text("utf-8"))
    backup = root / data["backup"]
    if backup.parent != root or not backup.name.startswith(".jiansz-backup-"):
        raise UpdateError("一条龙更新恢复记录无效。")
    # 回滚只移动本次明确记录的两个更新单元，保留配置和源码。
    failed = root / f".jiansz-failed-{time.time_ns()}"
    failed.mkdir()
    for name in data["names"]:
        if name not in {"OneDragon-RuntimeLauncher.exe", "OneDragon-Launcher.exe", ".runtime"}:
            raise UpdateError("一条龙更新恢复记录含未知文件。")
        old = backup / name
        live = root / name
        if old.exists():
            if live.exists():
                live.rename(failed / name)
            old.rename(live)
    journal.unlink()


def _update_onedragon(item: UpdateItem, stop, log, progress=None) -> UpdateItem:
    """按上游的启动器和配套运行时两个更新单元替换，可恢复中断。"""
    root = item.executable.parent
    journal = root / ".jiansz-update.json"
    _restore_onedragon(root, journal)
    runtime = item.executable.name.lower() == "onedragon-runtimelauncher.exe"
    suffix = "RuntimeLauncher.zip" if runtime else "Launcher.zip"
    name = f"ZenlessZoneZero-OneDragon-{suffix}"
    stage = root / ".install" / f"jiansz-update-{time.time_ns()}"
    stage.mkdir(parents=True)
    urls = [f"https://cnb.cool/{item.repository}/-/releases/download/{item.latest}/{name}",
            f"https://github.com/{item.repository}/releases/download/{item.latest}/{name}"]
    for index, url in enumerate(urls):
        source = "上游 CNB" if index == 0 else "上游 GitHub"
        log(f"正在从{source}下载启动器更新包，支持断点续传。")
        cache = root / ".install" / "jiansz-downloads" / hashlib.sha256(url.encode()).hexdigest()[:20]
        archive = cache / name
        try:
            download_file(url, archive, stop, log, progress)
            break
        except UpdateCancelled:
            raise
        except UpdateError as exc:
            if index == len(urls) - 1:
                raise
            log(f"{source}下载未完成：{exc} 切换另一个官方源。")
    report_progress(progress, "校验压缩包", message="检查文件结构和配套运行时")
    names = [item.executable.name] + ([".runtime"] if runtime else [])
    unpacked = stage / "unpacked"
    unpacked.mkdir()
    try:
        package = zipfile.ZipFile(archive)
    except zipfile.BadZipFile:
        archive.rename(archive.with_name(archive.name + f".invalid-{time.time_ns()}"))
        raise UpdateError("更新包不完整，已隔离缓存，下次会重新下载。") from None
    with package:
        size = 0
        selected = []
        for member in package.infolist():
            path = Path(member.filename)
            if path.is_absolute() or path.drive or ".." in path.parts or (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise UpdateError("更新包含无效路径。")
            if not path.parts or path.parts[0] not in names:
                continue
            size += member.file_size
            if size > 4 * 1024 ** 3:
                raise UpdateError("解压后更新包大小异常。")
            selected.append(member)
        extracted = 0
        last_progress = 0
        for member in selected:
            target = unpacked / member.filename
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with package.open(member) as source_file, target.open("wb") as output_file:
                    while chunk := source_file.read(256 * 1024):
                        if stop.is_set():
                            raise UpdateCancelled("已取消解压，下载缓存已保留。")
                        output_file.write(chunk)
                        extracted += len(chunk)
                        now = time.monotonic()
                        if now - last_progress >= .25 or extracted == size:
                            report_progress(progress, "解压运行时", completed=extracted, total=size, unit="bytes")
                            last_progress = now
            except zipfile.BadZipFile:
                package.close()
                archive.rename(archive.with_name(archive.name + f".invalid-{time.time_ns()}"))
                raise UpdateError("更新包文件校验失败，已隔离缓存，下次会重新下载。") from None
    staged = unpacked / item.executable.name
    if not staged.is_file() or (runtime and not list((unpacked / ".runtime").glob("python3[0-9]*.dll"))):
        raise UpdateError("更新包缺少启动器或配套运行时。")
    if stop.is_set():
        raise UpdateCancelled("已取消更新，原程序尚未替换。")
    report_progress(progress, "验证版本", message="运行新启动器的版本检查")
    # 上游先检查 src 是否为目录，再处理 --version；这里只查询冻结版本，不加载业务源码。
    (unpacked / "src").mkdir(exist_ok=True)
    output = run_output([str(staged), "--version"], unpacked)
    if item.latest not in output.split():
        raise UpdateError("更新包启动器版本与目标版本不一致。")
    _assert_idle(root)
    if stop.is_set():
        raise UpdateCancelled("已取消更新，原程序尚未替换。")
    report_progress(progress, "替换文件", total=len(names), message="正在备份旧版并替换，完成后才可启动")
    backup = root / f".jiansz-backup-{time.time_ns()}"
    backup.mkdir()
    pending = journal.with_suffix(".tmp")
    with pending.open("w", encoding="utf-8") as output:
        json.dump({"backup": backup.name, "names": names}, output)
        output.flush()
        os.fsync(output.fileno())
    pending.replace(journal)
    try:
        for index, component in enumerate(names, 1):
            live = root / component
            if live.exists():
                live.rename(backup / component)
            (unpacked / component).rename(live)
            report_progress(progress, "替换文件", completed=index, total=len(names))
        if _stable_version(local_version(item)) != _stable_version(item.latest):
            raise UpdateError("替换后版本校验失败。")
        journal.unlink()
    except Exception:
        _restore_onedragon(root, journal)
        raise
    item.current = local_version(item)
    item.status = "已是最新"
    item.detail = f"启动器和配套运行时已更新，旧版保存在 {backup}。源码由原启动器同步。"
    report_progress(progress, "更新完成", completed=1, total=1)
    return item


def update_item(item: UpdateItem, stop, log, timeout: int = 5400, progress=None) -> UpdateItem:
    if item.status not in {"有更新", "更新失败"} or not item.latest:
        raise UpdateError("请先成功检查版本，再执行更新。")
    root = item.executable.parent
    if stop.is_set():
        raise UpdateCancelled("已取消更新。")
    callback = progress
    def progress(event):
        item.progress = event
        if callback:
            callback(event)
    _assert_idle(root)
    if item.key == "onedragon":
        return _update_onedragon(item, stop, log, progress)
    if item.key == "march7th":
        command = [str(root / "m7a.exe"), "app_update", "--after-finish", "None"]
    elif item.key == "bettergi":
        command = [str(root / "BetterGI.update.exe"), "-I", "--source", "cnb"]
    elif item.key == "okww":
        command = [str(item.executable), "-c", "start", "--auto-start", "false", "--update-method", "auto"]
    else:
        raise UpdateError("此工具尚未适配自动更新。")
    if not Path(command[0]).is_file():
        raise UpdateError("未找到原工具的更新程序。")
    # 输出落到文件，避免无人读取的管道阻塞原工具。
    output_dir = Path(os.environ.get("LOCALAPPDATA", str(root))) / "Jiansz/update-logs"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{item.key}-{time.time_ns()}.log"
    with output_path.open("wb") as output:
        process = native_popen(command, cwd=root, stdout=output, stderr=subprocess.STDOUT)
    log(f"已调用原工具更新入口，日志保存在 {output_path}")
    report_progress(progress, "原工具更新", message="等待原更新器输出；未提供百分比时显示活动进度")
    deadline = time.monotonic() + timeout
    stable_since = None
    log_position = 0
    log_pending = ""
    last_progress = time.monotonic()
    while time.monotonic() < deadline:
        if stop.wait(2):
            raise UpdateCancelled("已停止等待，原工具更新可能仍在进行，请在原工具确认结果。")
        with output_path.open("rb") as output:
            output.seek(log_position)
            chunk = output.read(128 * 1024)
            log_position = output.tell()
        if chunk:
            text = log_pending + chunk.decode("utf-8", errors="replace")
            lines = re.split(r"[\r\n]", text)
            log_pending = lines.pop()[-2000:]
            # 一些更新器只覆写当前行、不写换行，仍可显示其当前百分比。
            candidates = lines + ([log_pending] if log_pending else [])
            matches = [(line, re.findall(r"(?<![\d.])(\d{1,3}(?:\.\d+)?)\s*%", line)) for line in candidates]
            latest = next(((line, values[-1]) for line, values in reversed(matches) if values), None)
            if latest and 0 <= float(latest[1]) <= 100:
                report_progress(progress, "原工具更新", completed=float(latest[1]), total=100,
                                message=re.sub(r"\x1b\[[0-9;]*m", "", latest[0])[-180:])
                last_progress = time.monotonic()
        if time.monotonic() - last_progress >= 8:
            report_progress(progress, "原工具更新", message="正在等待原工具更新与版本确认；尚无新的百分比")
            last_progress = time.monotonic()
        try:
            current = local_version(item)
            installed = _stable_version(current) >= _stable_version(item.latest)
        except (OSError, ValueError, UpdateError):
            installed = False
        exit_code = process.poll()
        if installed and (exit_code == 0 or item.key == "okww"):
            stable_since = stable_since or time.monotonic()
            if time.monotonic() - stable_since >= 5:
                item.current = current
                item.status = "已是最新"
                item.detail = "已重新读取本地版本确认更新。"
                report_progress(progress, "更新完成", completed=1, total=1)
                return item
        else:
            stable_since = None
        if exit_code not in (None, 0):
            raise UpdateError(f"原更新程序退出码 {exit_code}，请查看 {output_path}")
    raise UpdateError(f"未确认更新完成，请查看原工具及 {output_path}")
