# PyInstaller spec for the JellyPlex-Watched desktop app.
# Build from the repository root:  pyinstaller packaging/windows/jellyplex-watched.spec
#
# Produces dist/JellyPlex-Watched/ containing:
#   JellyPlex-Watched.exe  - windowed app (native window via pywebview)
#   jpw-worker.exe         - console worker that runs validate/test/sync,
#                            launched hidden by the app
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
ICON = os.path.join(SPECPATH, "icon.ico")
IS_WIN = sys.platform == "win32"

common_hidden = (
    collect_submodules("src")
    + collect_submodules("plexapi")
    + collect_submodules("pydantic_settings")
    + ["main", "gui", "gui.app", "gui.worker"]
)
datas = [(os.path.join(ROOT, "gui", "static"), os.path.join("gui", "static"))]

app_a = Analysis(
    [os.path.join(ROOT, "desktop.py")],
    pathex=[ROOT],
    datas=datas,
    hiddenimports=common_hidden
    + collect_submodules("uvicorn")
    + collect_submodules("webview"),
    excludes=["tkinter", "pytest"],
)
worker_a = Analysis(
    [os.path.join(ROOT, "jpw_worker.py")],
    pathex=[ROOT],
    hiddenimports=common_hidden,
    excludes=["tkinter", "pytest", "webview", "uvicorn", "fastapi"],
)

app_pyz = PYZ(app_a.pure)
worker_pyz = PYZ(worker_a.pure)

app_exe = EXE(
    app_pyz,
    app_a.scripts,
    [],
    exclude_binaries=True,
    name="JellyPlex-Watched",
    console=False,
    icon=ICON if IS_WIN else None,
    upx=False,
)
worker_exe = EXE(
    worker_pyz,
    worker_a.scripts,
    [("X utf8_mode=1", None, "OPTION")],
    exclude_binaries=True,
    name="jpw-worker",
    console=True,
    icon=ICON if IS_WIN else None,
    upx=False,
)

coll = COLLECT(
    app_exe,
    worker_exe,
    app_a.binaries,
    app_a.datas,
    worker_a.binaries,
    worker_a.datas,
    upx=False,
    name="JellyPlex-Watched",
)
