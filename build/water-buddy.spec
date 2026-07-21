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
    # QtNetwork is deliberately NOT excluded: single_instance.py uses
    # QLocalServer from it. Excluding a module the app imports produces a build
    # that runs fine from source and crashes only once packaged, which is the
    # most annoying class of bug there is.
    excludes=[
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtWebEngineCore",
        "PySide6.Qt3DCore",
        "PySide6.QtMultimedia",
        "tkinter",
    ],
    noarchive=False,
)

# Excluding a PySide6 *module* above stops Python importing it, but the Qt
# *DLLs* still get collected because PyInstaller sees them as dependencies of
# the Qt libraries it does keep. Dropping them by filename is what actually
# shrinks the download.
#
# Everything below is safe for a plain QtWidgets app:
#   opengl32sw   - software OpenGL fallback. Widgets render through the raster
#                  engine, so this is only used by QML/Quick scenes.
#   Qt6Quick/Qml - the QML runtime. We build the UI in Python, not QML.
#   Qt6Pdf       - PDF rendering.
#   Qt6Network*  - kept: single_instance.py needs QLocalServer.
#
# If a trimmed build ever fails to start, comment this block out first to find
# out whether a missing DLL is the cause.
UNUSED_DLLS = {
    "opengl32sw.dll",
    "qt6quick.dll",
    "qt6qml.dll",
    "qt6qmlmodels.dll",
    "qt6qmlmeta.dll",
    "qt6qmlworkerscript.dll",
    "qt6pdf.dll",
    "qt6opengl.dll",
    "qt6virtualkeyboard.dll",
}

a.binaries = [entry for entry in a.binaries
              if Path(entry[0]).name.lower() not in UNUSED_DLLS]

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
