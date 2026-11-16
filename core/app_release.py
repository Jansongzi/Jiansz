"""检查简小松本身的官方稳定版。"""
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen

from packaging.version import Version

from core.core import APP_VERSION, PROJECT_URL


RELEASES_URL = f"{PROJECT_URL}/releases"


def check_app_release() -> dict:
    request = Request(f"{RELEASES_URL}/latest", headers={"User-Agent": f"Jiansz/{APP_VERSION}"})
    status = {"current": APP_VERSION, "latest": ""}
    try:
        with urlopen(request, timeout=10) as response:
            release = urlsplit(response.geturl())
        expected = urlsplit(RELEASES_URL)
        if (release.scheme, release.netloc) != (expected.scheme, expected.netloc):
            raise ValueError("unexpected release URL")
        path = release.path.rstrip("/")
        if path == expected.path:
            return {**status, "state": "no_release"}
        prefix = f"{expected.path}/tag/"
        if not path.startswith(prefix):
            raise ValueError("unexpected release path")
        tag = unquote(path[len(prefix):])
        version = Version(tag)
        if version.is_prerelease or version.is_devrelease:
            return {**status, "state": "no_release"}
        return {**status, "latest": tag, "state": "available" if version > Version(APP_VERSION) else "current"}
    except (OSError, ValueError):
        return {**status, "state": "error"}
