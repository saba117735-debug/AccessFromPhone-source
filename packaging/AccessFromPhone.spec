# PyInstaller recipe: a folder with AccessFromPhone.exe (no console window) and everything it needs.
# Built by build.ps1:   pyinstaller packaging/AccessFromPhone.spec
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo)

HERE = Path(SPECPATH)
ROOT = HERE.parent
VERSION = "0.0.0"
for line in (ROOT / "src" / "handoff" / "_build.py").read_text(encoding="utf-8-sig").splitlines():  # -sig: PowerShell may add a hidden marker
    if line.startswith("VERSION"):
        VERSION = line.split("=", 1)[1].strip().strip('"').strip("'")
numbers = tuple(int(p) for p in (VERSION.split(".") + ["0", "0", "0"])[:4])

# What Windows shows under Properties > Details, and in Task Manager.
version = VSVersionInfo(
    ffi=FixedFileInfo(filevers=numbers, prodvers=numbers, mask=0x3F, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0),
    kids=[
        StringFileInfo([StringTable("040904B0", [
            StringStruct("CompanyName", "AccessFromPhone"),
            StringStruct("FileDescription", "AccessFromPhone"),
            StringStruct("FileVersion", VERSION),
            StringStruct("InternalName", "AccessFromPhone"),
            StringStruct("OriginalFilename", "AccessFromPhone.exe"),
            StringStruct("ProductName", "AccessFromPhone"),
            StringStruct("ProductVersion", VERSION),
        ])]),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

hidden = (
    collect_submodules("uvicorn") + collect_submodules("websockets") + collect_submodules("zeroconf")
    + ["pystray._win32", "PIL._tkinter_finder", "win32timezone"]
)

a = Analysis(
    [str(HERE / "run_desktop.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[(str(ROOT / "src" / "handoff" / "web"), "handoff/web")],
    hiddenimports=hidden,
    excludes=["tkinter", "pytest", "IPython", "matplotlib", "numpy.tests"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="AccessFromPhone",
    console=False,  # no terminal window: it lives in the tray
    icon=str(HERE / "build" / "app.ico"),
    version=version,
    upx=False,  # compressing the program makes antivirus tools more suspicious, not less
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="AccessFromPhone")
