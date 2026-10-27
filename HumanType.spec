# PyInstaller build: one folder with HumanType.exe (starts faster and trips antiviruses less than one file).
# Run through build.bat, or: .venv\Scripts\python -m PyInstaller --noconfirm --clean HumanType.spec

a = Analysis(
    ["run.pyw"],
    datas=[
        ("assets/dict", "assets/dict"),
        ("assets/icon.ico", "assets"),
        ("assets/icon.png", "assets"),
        ("assets/app.svg", "assets"),
        ("assets/tray.svg", "assets"),
    ],
    hiddenimports=["pythoncom", "pywintypes"],
    excludes=[
        "tkinter", "unittest", "pydoc", "PIL",
        "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets", "PySide6.QtMultimedia", "PySide6.Qt3DCore", "PySide6.QtPdf",
    ],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="HumanType",
    icon="assets/icon.ico",
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="HumanType", upx=False)
