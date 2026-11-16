# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from runpy import run_path

from PyInstaller.utils.win32 import versioninfo

app_version = run_path(str(Path(SPECPATH) / 'core/version.py'))['APP_VERSION'].removeprefix('v')
version_tuple = (*map(int, app_version.split('.')), 0)
windows_version = versioninfo.VSVersionInfo(
    ffi=versioninfo.FixedFileInfo(filevers=version_tuple, prodvers=version_tuple,
                                 mask=0x3F, flags=0, OS=0x40004, fileType=1,
                                 subtype=0, date=(0, 0)),
    kids=[
        versioninfo.StringFileInfo([versioninfo.StringTable('080404b0', [
            versioninfo.StringStruct('FileDescription', '简小松·脚本调度助手'),
            versioninfo.StringStruct('FileVersion', app_version),
            versioninfo.StringStruct('ProductName', 'Jiansz'),
            versioninfo.StringStruct('ProductVersion', app_version),
            versioninfo.StringStruct('OriginalFilename', 'Jiansz.exe'),
        ])]),
        versioninfo.VarFileInfo([versioninfo.VarStruct('Translation', [0x0804, 1200])]),
    ],
)

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('assets', 'assets'), ('docs', 'docs'), ('ui/web', 'ui/web')],
    hiddenimports=['webview.platforms.edgechromium', 'bottle', 'pythonnet', 'clr_loader', 'PIL.ImageGrab'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
# Qt on Windows uses the OS ICU API. Exclude incompatible ICU DLLs found on PATH.
a.binaries = [item for item in a.binaries if item[0].lower() not in {"icuuc.dll", "icudt78.dll"}]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Jiansz',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=True,
    version=windows_version,
    icon=['assets/icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Jiansz',
)
