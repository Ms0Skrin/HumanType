# PyInstaller build: one folder with HumanType.exe (starts faster and trips antiviruses less than one file).
# Run through build.bat, or: .venv\Scripts\python -m PyInstaller --noconfirm --clean HumanType.spec

from PyInstaller.utils.hooks import collect_data_files, collect_submodules
import comtypes.client

# UI Automation's COM wrappers are generated on first use; make them now so they go into the build.
comtypes.client.GetModule("UIAutomationCore.dll")

a = Analysis(
    ["run.pyw"],
    datas=[
        ("assets/dict", "assets/dict"),
        ("assets/icon.ico", "assets"),
        ("assets/icon.png", "assets"),
        ("assets/app.svg", "assets"),
        ("assets/tray.svg", "assets"),
        *collect_data_files("pymorphy3_dicts_ru"),  # Russian morphology for the detector
    ],
    hiddenimports=["pythoncom", "pywintypes", *collect_submodules("comtypes.gen"), *collect_submodules("pymorphy3")],
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
