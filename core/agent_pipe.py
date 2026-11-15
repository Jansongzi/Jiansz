"""Local, per-user endpoint for starting the saved task queue."""
import hashlib
import json
import os
import threading
import time

import pywintypes
import win32api
import win32file
import win32pipe
import win32security
import win32ts
import winerror


class InstanceExists(Exception):
    pass


def _user_sid():
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
    try:
        sid, _ = win32security.GetTokenInformation(token, win32security.TokenUser)
        return win32security.ConvertSidToStringSid(sid)
    finally:
        token.Close()


def pipe_path(base_dir):
    directory = os.path.normcase(str(base_dir.resolve()))
    digest = hashlib.sha256(directory.encode("utf-8")).hexdigest()[:24]
    session = win32ts.ProcessIdToSessionId(os.getpid())
    return rf"\\.\pipe\Jiansz-{_user_sid()}-{session}-{digest}"


def send_start(base_dir):
    return json.loads(win32pipe.CallNamedPipe(pipe_path(base_dir), b"start\n", 2048, 4000))


class AgentPipe:
    def __init__(self, base_dir):
        security = pywintypes.SECURITY_ATTRIBUTES()
        security.SECURITY_DESCRIPTOR = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
            f"D:P(A;;GA;;;{_user_sid()})(A;;GA;;;SY)S:(ML;;NW;;;ME)", 1
        )
        try:
            self._handle = win32pipe.CreateNamedPipe(
                pipe_path(base_dir),
                win32pipe.PIPE_ACCESS_DUPLEX | win32pipe.FILE_FLAG_FIRST_PIPE_INSTANCE,
                win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE
                | win32pipe.PIPE_NOWAIT | win32pipe.PIPE_REJECT_REMOTE_CLIENTS,
                1, 4096, 4096, 1000, security,
            )
        except pywintypes.error as exc:
            if exc.winerror in (winerror.ERROR_ACCESS_DENIED, winerror.ERROR_PIPE_BUSY):
                raise InstanceExists from exc
            raise

    def serve(self, command):
        threading.Thread(target=self._listen, args=(command,), daemon=True).start()

    def _listen(self, command):
        while True:
            try:
                status = win32pipe.ConnectNamedPipe(self._handle)
            except pywintypes.error as exc:
                status = exc.winerror
            if status == winerror.ERROR_PIPE_LISTENING:
                time.sleep(0.05)
                continue
            if status not in (0, None, winerror.ERROR_PIPE_CONNECTED, winerror.ERROR_NO_DATA):
                return
            try:
                if status != winerror.ERROR_NO_DATA:
                    self._respond(command)
            except pywintypes.error:
                pass
            finally:
                try:
                    win32pipe.DisconnectNamedPipe(self._handle)
                except pywintypes.error:
                    return

    def _respond(self, command):
        deadline = time.monotonic() + 2
        request = b""
        while len(request) < 16 and not request.endswith(b"\n") and time.monotonic() < deadline:
            try:
                error, part = win32file.ReadFile(self._handle, 16 - len(request))
            except pywintypes.error as exc:
                if exc.winerror != winerror.ERROR_NO_DATA:
                    raise
                time.sleep(0.025)
                continue
            request += part
            if error not in (0, winerror.ERROR_MORE_DATA):
                return
        action = {b"start\n": "start", b"status\n": "status"}.get(request)
        try:
            reply = command(action) if action else {"status": "invalid", "message": "未知命令。"}
        except Exception:
            reply = {"status": "error", "message": "简小松无法处理本机请求。"}
        win32file.WriteFile(self._handle, json.dumps(reply).encode("ascii") + b"\n")
        # The reader needs time to receive the response before DisconnectNamedPipe drops it.
        time.sleep(0.15)
