# PyInstaller build spec.  Build with:
#     .venv\Scripts\python.exe -m PyInstaller build\water-buddy.spec
#
# Two settings here matter more than the rest:
#
#   console=False  -- without it, a black terminal window sits behind the app
#                     forever. For a tray app that is the difference between
#                     looking finished and looking like a script.
#
#   datas          -- assets/ is NOT automatically included. PyInstaller only
#                     follows Python imports, and nothing imports a PNG. Files
#                     listed here get unpacked at runtime into sys._MEIPASS,
#                     which is exactly what config._assets_root() looks for.

from pathlib import Path

project_root = Path(SPECPATH).parent

a = Analysis(
    [str(project_root / "run.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[(str(project_root / "assets"), "assets")],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    # Qt ships a lot we never touch. Excluding it cuts the bundle substantially.
    excludes=[
        "PySide6.QtNetwork",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtWebEngineCore",
        "PySide6.Qt3DCore",
        "PySide6.QtMultimedia",
        "tkinter",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WaterBuddy",
    debug=False,
    strip=False,
    upx=False,
    console=False,          # no terminal window
    icon=None,              # drop a .ico here once you have one
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="WaterBuddy",
)
