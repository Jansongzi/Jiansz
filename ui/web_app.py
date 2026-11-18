# -*- coding: utf-8 -*-
"""基于 PyWebView (Edge WebView2) + Bottle 本地微服务的现代桌面外壳。"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys
import sysconfig
from threading import Thread

from bottle import Bottle, abort, static_file
import webview

from core import APP_NAME, app_dir
from core.agent_pipe import AgentPipe, InstanceExists, send_start
from ui.web_backend import JianszWebBridge, resolve_asset_file, resolve_doc_file


def _resolve_web_root(base_dir: Path) -> Path:
    candidate = base_dir / "ui" / "web"
    if candidate.exists():
        return candidate
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundled = Path(meipass) / "ui" / "web"
        if bundled.exists():
            return bundled
    return candidate


def create_wsgi_app(base_dir: Path) -> Bottle:
    app = Bottle()
    web_root = _resolve_web_root(base_dir)

    def safe_parts(filepath):
        path = Path(filepath)
        if path.anchor or ".." in path.parts:
            abort(403)
        return path.parts

    def serve_file(target, roots):
        if target is None or not target.is_file():
            abort(404)
        if not any(target.resolve().is_relative_to(root.resolve()) for root in roots):
            abort(403)
        return static_file(target.name, root=str(target.parent))

    def resource_roots(name):
        roots = [base_dir / name]
        if getattr(sys, "_MEIPASS", None):
            roots.append(Path(sys._MEIPASS) / name)
        return roots

    @app.route("/")
    def index():
        return static_file("index.html", root=str(web_root))

    @app.route("/assets/<filepath:path>")
    def serve_assets(filepath: str):
        return serve_file(resolve_asset_file(base_dir, *safe_parts(filepath)), resource_roots("assets"))

    @app.route("/docs/<filepath:path>")
    def serve_docs(filepath: str):
        return serve_file(resolve_doc_file(base_dir, *safe_parts(filepath)), resource_roots("docs"))

    @app.route("/<filepath:path>")
    def serve_static(filepath: str):
        return serve_file(web_root.joinpath(*safe_parts(filepath)), [web_root])

    return app


def _round_window_corners(window) -> None:
    """将 WinForms 外框裁成与网页面板相同的 16px 圆角。"""
    from System import Action

    form = window.native
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    get_dpi = getattr(user32, "GetDpiForWindow", None)
    if get_dpi is not None:
        get_dpi.argtypes = [wintypes.HWND]
        get_dpi.restype = wintypes.UINT
    set_region = user32.SetWindowRgn
    set_region.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
    create_region = gdi32.CreateRoundRectRgn
    create_region.argtypes = [ctypes.c_int] * 6
    create_region.restype = wintypes.HRGN
    delete_object = gdi32.DeleteObject
    delete_object.argtypes = [wintypes.HGDIOBJ]

    previous = None

    def apply_region(*_):
        nonlocal previous
        state = str(form.WindowState)
        if state == "Minimized" or form.IsDisposed:
            return
        hwnd = form.Handle.ToInt64()
        width, height = form.Width, form.Height
        dpi = (get_dpi(hwnd) if get_dpi is not None else 96) or 96
        diameter = 0 if state == "Maximized" else round(2 * 16 * dpi / 96)
        shape = (hwnd, width, height, diameter)
        if shape == previous or min(width, height) <= 0:
            return
        if not diameter:
            if set_region(hwnd, None, True):
                previous = shape
            return
        region = create_region(0, 0, width + 1, height + 1, diameter, diameter)
        if region:
            if set_region(hwnd, region, True):
                previous = shape
            else:
                delete_object(region)

    def install():
        form.Resize += apply_region
        form.HandleCreated += apply_region
        if hasattr(form, "DpiChanged"):
            form.DpiChanged += apply_region
        apply_region()

    # pywebview 的 shown 回调由后台线程执行，WinForms 事件在窗口线程注册。
    form.BeginInvoke(Action(install))


def _install_tray(window, bridge: JianszWebBridge) -> None:
    from System import Action
    from System.Drawing import SystemIcons
    from System.Windows.Forms import ContextMenuStrip, FormWindowState, NotifyIcon

    form = window.native

    def install():
        tray = NotifyIcon()
        menu = ContextMenuStrip()
        tray.Icon = form.Icon or SystemIcons.Application
        tray.Text = APP_NAME
        tray.ContextMenuStrip = menu

        def restore(*_):
            form.Show()
            if form.WindowState == FormWindowState.Minimized:
                form.WindowState = FormWindowState.Normal
            form.Activate()

        def hide():
            tray.Visible = True
            form.Hide()

        def exit_app(*_):
            Thread(target=bridge.win_close, daemon=True).start()

        def dispose(*_):
            bridge._hide_in_tray = None
            tray.Visible = False
            tray.Dispose()
            menu.Dispose()

        menu.Items.Add("打开主窗口").Click += restore
        menu.Items.Add("退出").Click += exit_app
        tray.DoubleClick += restore
        form.FormClosed += dispose
        bridge._hide_in_tray = lambda: form.BeginInvoke(Action(hide))
        tray.Visible = True

    form.BeginInvoke(Action(install))


def _webview2_startup_error() -> str:
    machine = {"win32": "x86", "win-amd64": "x64", "win-arm64": "arm64"}.get(sysconfig.get_platform())
    if machine is None:
        return "简小松需要 Windows 及 Microsoft Edge WebView2 Runtime。"
    library = Path(webview.__file__).parent / "lib" / "runtimes" / f"win-{machine}" / "native" / "WebView2Loader.dll"
    try:
        loader = ctypes.WinDLL(str(library))
    except OSError:
        return "简小松缺少 WebView2 加载组件。请先将整个压缩包解压到同一目录，再打开 Jiansz.exe。"
    query = loader.GetAvailableCoreWebView2BrowserVersionString
    query.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    query.restype = ctypes.c_long
    version = ctypes.c_void_p()
    try:
        result = query(webview.settings.get("WEBVIEW2_RUNTIME_PATH") or None, ctypes.byref(version))
        if result == 0 and version.value:
            return ""
    finally:
        free = ctypes.windll.ole32.CoTaskMemFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = None
        free(version)
    return "请先安装 Microsoft Edge WebView2 Runtime，再重新打开简小松。"


def run_app(*, start_daily: bool = False, agent_host: bool = False) -> int:
    base_dir = app_dir()
    os.chdir(base_dir)
    try:
        endpoint = AgentPipe(base_dir)
    except InstanceExists:
        if start_daily:
            reply = send_start(base_dir)
            return 0 if reply["status"] in {"accepted", "running", "updating"} else 1
        return 0
    prerequisite_error = _webview2_startup_error()
    if prerequisite_error:
        ctypes.windll.user32.MessageBoxW(None, prerequisite_error, APP_NAME, 0x10)
        return 1
    bridge = JianszWebBridge(base_dir=base_dir, agent_host=agent_host)
    wsgi_app = create_wsgi_app(base_dir)

    window = webview.create_window(
        title=APP_NAME,
        url=wsgi_app,
        js_api=bridge,
        width=1180,
        height=760,
        min_size=(980, 640),
        frameless=True,
        easy_drag=False,
        shadow=True,
        background_color="#090D16",
        text_select=True,
    )
    bridge._attach_window(window)
    window.events.closing += bridge._on_window_closing
    window.events.shown += lambda: _round_window_corners(window)
    window.events.shown += lambda: _install_tray(window, bridge)
    window.events.shown += bridge._migrate_windows_startup

    def start_endpoint():
        if start_daily:
            bridge._agent_command("start")
        endpoint.serve(bridge._agent_command)

    window.events.shown += start_endpoint

    icon_file = resolve_asset_file(base_dir, "icon.ico")
    webview.start(
        gui="edgechromium",
        debug=False,
        icon=str(icon_file) if icon_file else None,
    )
    return 0
