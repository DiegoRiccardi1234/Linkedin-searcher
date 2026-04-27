# JobFinder.spec — PyInstaller config for the standalone Windows build.
# Build:  pyinstaller JobFinder.spec --noconfirm

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

hiddenimports: list[str] = []
hiddenimports += collect_submodules("app.migrations")
hiddenimports += collect_submodules("app.providers")
hiddenimports += [
    "openai",
    "groq",
    "anthropic",
    "google.generativeai",
    "cerebras.cloud.sdk",
    "uvicorn.logging",
    "uvicorn.protocols",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]

datas: list[tuple[str, str]] = [
    ("web", "web"),
    ("app/prompts", "app/prompts"),
    ("app/migrations", "app/migrations"),
]
try:
    datas += collect_data_files("jobspy", include_py_files=False)
except Exception:
    pass
try:
    datas += collect_data_files("tls_client", include_py_files=False)
except Exception:
    pass

excludes = [
    "tests",
    "matplotlib",
    "tkinter",
    "PyQt5",
    "PySide2",
    "PyQt6",
    "PySide6",
]

# ─── Main app: JobFinder.exe ──────────────────────────────────────
a_main = Analysis(
    ["scripts/launch_exe.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    cipher=block_cipher,
    noarchive=False,
)

# ─── Sibling: Updater.exe ─────────────────────────────────────────
a_upd = Analysis(
    ["scripts/updater.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    cipher=block_cipher,
    noarchive=False,
)

# Share dependencies so Updater.exe doesn't double the bundle size.
MERGE(
    (a_main, "JobFinder", "JobFinder"),
    (a_upd, "Updater", "Updater"),
)

pyz_main = PYZ(a_main.pure, a_main.zipped_data, cipher=block_cipher)
pyz_upd = PYZ(a_upd.pure, a_upd.zipped_data, cipher=block_cipher)

exe_main = EXE(
    pyz_main,
    a_main.scripts,
    [],
    exclude_binaries=True,
    name="JobFinder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    icon=None,
)

exe_upd = EXE(
    pyz_upd,
    a_upd.scripts,
    [],
    exclude_binaries=True,
    name="Updater",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    icon=None,
)

coll = COLLECT(
    exe_main,
    exe_upd,
    a_main.binaries,
    a_main.zipfiles,
    a_main.datas,
    a_upd.binaries,
    a_upd.zipfiles,
    a_upd.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="JobFinder",
)
