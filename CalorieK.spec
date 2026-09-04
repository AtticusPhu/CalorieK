# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from importlib.util import find_spec

project_dir = Path(SPECPATH)
pyside_spec = find_spec("PySide6")
if pyside_spec is None or pyside_spec.origin is None:
    raise RuntimeError("PySide6 must be installed before building CalorieK")
pyside_dir = Path(pyside_spec.origin).resolve().parent
msvc_patterns = (
    "concrt140.dll",
    "msvcp140*.dll",
    "vcamp140.dll",
    "vccorlib140.dll",
    "vcomp140.dll",
    "vcruntime140*.dll",
)
qt_runtime_binaries = sorted(
    {(str(path), "PySide6") for pattern in msvc_patterns for path in pyside_dir.glob(pattern)}
)

a = Analysis(
    [str(project_dir / "main.py")],
    pathex=[str(project_dir)],
    # PyInstaller 6.22 does not currently discover every transitive MSVC
    # runtime shipped with PySide6 6.11 on Windows.  Keep the coherent runtime
    # set beside Qt's DLLs so QtWidgets can load on clean systems.
    binaries=qt_runtime_binaries,
    datas=[
        (str(project_dir / "app" / "db" / "schema.sql"), "app/db"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# Qt 6.11 links against Windows' system ICU shim (icuuc.dll). If a developer's
# PATH contains Poppler or another full ICU distribution, PyInstaller may copy
# that unrelated DLL into _internal, where it shadows the system shim and makes
# QtCore fail with ERROR_PROC_NOT_FOUND. Drop that accidental environment leak
# and its data DLL; supported Windows 10/11 systems provide the intended shim.
a.binaries = [
    entry
    for entry in a.binaries
    if Path(entry[0]).name.lower() != "icuuc.dll"
    and not Path(entry[0]).name.lower().startswith("icudt")
]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CalorieK",
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
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="CalorieK",
)
