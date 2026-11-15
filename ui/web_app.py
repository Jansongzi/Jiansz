# -*- coding: utf-8 -*-
"""基于 PyWebView (Edge WebView2) + Bottle 本地微服务的现代桌面外壳。"""
from __future__ import annotations

from pathlib import Path
import sys

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


def run_app(*, start_daily: bool = False, agent_host: bool = False) -> int:
    base_dir = app_dir()
    try:
        endpoint = AgentPipe(base_dir)
    except InstanceExists:
        if start_daily:
            reply = send_start(base_dir)
            return 0 if reply["status"] in {"accepted", "running", "updating"} else 1
        return 0
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
