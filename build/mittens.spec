# PyInstaller spec for Mittens & Pence.
#
# The executable and bundle are named 'Mittens and Pence', without the
# ampersand: `&` is a command separator in Windows batch, so a path
# containing it breaks any script that references it unquoted.
#
#   pip install pyinstaller
#   pyinstaller build/mittens.spec --noconfirm
#
# Two shapes, one spec:
#
#   pyinstaller build/mittens.spec --noconfirm            -> dist/Mittens and Pence/Mittens and Pence.exe
#       A folder. Starts in about a second and antivirus barely looks at it. Right for
#       your own machine, awkward to send to anyone.
#
#   set MITTENS_ONEFILE=1  (Windows)  /  export MITTENS_ONEFILE=1  (Mac)
#   pyinstaller build/mittens.spec --noconfirm            -> dist/Mittens and Pence.exe
#       One self-contained file that unpacks itself to a temp folder each time it runs.
#       Slower to start (five to fifteen seconds) and more likely to make SmartScreen or
#       antivirus grumble, but it is a single thing you can put on a Drive link and say
#       "download this and double-click it".
#
# build_windows.bat and build_macos.sh build both.
#
# PyInstaller cannot cross-compile: build the Windows executable on Windows, the Mac
# one on a Mac. build_windows.bat and build_macos.sh do the whole thing in one step.

import os
import pathlib
import sys

ROOT = pathlib.Path(SPECPATH).parent          # noqa: F821 — SPECPATH is injected
sys.path.insert(0, str(ROOT))
from kestrel import config as _cfg             # noqa: E402 — needs ROOT on the path

# The python package is still called `kestrel`, deliberately — renaming it would move
# everybody's data directory. The blanket kestrel→mithapp rename rewrote this line to
# `ROOT / "mithapp"`, which does not exist, so `datas` pointed at nothing: the build
# would have shipped with **no web interface and no institution list** and started to a
# blank window. Exactly the bug that hit tools/build_institutions.py. The assertion
# below is why it cannot happen quietly again.
PKG = ROOT / "kestrel"
assert (PKG / "web" / "static" / "app.js").exists(), (
    f"the app's front end is not where the spec is looking: {PKG / 'web' / 'static'}")
assert (PKG / "institutions" / "data").is_dir(), (
    f"the institution registry is not where the spec is looking: {PKG / 'institutions'}")

block_cipher = None

datas = [
    (str(PKG / "web" / "static"), "web/static"),
    (str(PKG / "institutions" / "data"), "institutions/data"),
]

hiddenimports = [
    "openpyxl", "openpyxl.chart", "openpyxl.styles", "openpyxl.formatting.rule",
    "openpyxl.worksheet.datavalidation", "openpyxl.utils",
    "sqlite3", "email.mime.text", "webbrowser",
    # providers register themselves on import, so name them explicitly
    "kestrel.providers.trading212", "kestrel.providers.investec",
    "kestrel.providers.truelayer", "kestrel.providers.gocardless",
    "kestrel.providers.ibkr", "kestrel.providers.monzo",
    "kestrel.providers.starling", "kestrel.providers.crypto",
    "kestrel.providers.enablebanking",
    # PDF statements. pdfplumber pulls pdfminer.six and Pillow; without these the
    # frozen build imports fine and then falls over the first time someone drops a
    # PDF on the import screen.
    "pdfplumber", "pdfminer", "pdfminer.high_level", "pdfminer.layout",
    "PIL", "PIL.Image",
]

# Optional extras: bundled when installed, silently skipped when not.
for opt in ("keyring", "keyring.backends.Windows", "keyring.backends.macOS",
            "keyring.backends.SecretService", "cryptography", "webview"):
    try:
        __import__(opt)
        hiddenimports.append(opt)
    except Exception:
        pass

# Two names had to come OUT of this list, both for the same reason: excluding something
# that is needed is invisible until the exact machine that needs it tries.
#
#   Pillow — pdfplumber depends on it. Excluding it built an app that started perfectly
#   and died the moment a PDF statement was dropped on the import screen.
#
#   distutils — removed from the standard library in Python 3.12, and now supplied by
#   setuptools. PyInstaller's own `hook-distutils` aliases the vendored copy back to the
#   name `distutils`, and aliasing onto an EXCLUDED name is a hard error:
#       ValueError: Target module "distutils" already imported as ExcludedModule
#   It needs three things at once — Python 3.12+, setuptools present, and any import
#   chain that reaches distutils (on Windows, pywebview → pythonnet does it). So the
#   build passed on 3.11 and on Linux, and failed on a Windows PC running 3.14.
#
# `setuptools` and `pip` came out at the same time. They are not dependencies, but
# PyInstaller's hooks alias several names *into* setuptools' vendored tree, and excluding
# the tree they point at invites the same failure by a different door. A few MB of build
# size is a cheap price for removing a whole class of "works here, not there".
#
# ALIASED_BY_PYINSTALLER below is the list of names its hooks alias. None of them may
# ever appear in `excludes`; a test checks that from the other side.
ALIASED_BY_PYINSTALLER = ("distutils", "backports", "importlib_metadata", "jaraco")

excludes = [
    "tkinter", "unittest", "pydoc_data", "test",
    "numpy", "pandas", "matplotlib", "scipy", "IPython", "notebook",
    "pytest", "playwright",
]
assert not set(excludes) & set(ALIASED_BY_PYINSTALLER), (
    "excluding a name PyInstaller aliases makes the build fail with "
    "'already imported as ExcludedModule'")

a = Analysis(                                  # noqa: F821
    [str(ROOT / "run_kestrel.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)     # noqa: F821

# Per platform, not "the first file that happens to exist": Windows needs .ico and
# macOS needs .icns, and handing PyInstaller the wrong one is either a warning and a
# missing icon or a hard failure. Now that both files exist, the old first-match loop
# would have given every Mac build a .ico.
_ICON_FOR = {"win32": "kestrel.ico", "darwin": "kestrel.icns"}
icon = None
_wanted = PKG / "resources" / _ICON_FOR.get(sys.platform, "kestrel.png")
if _wanted.exists():
    icon = str(_wanted)
elif (PKG / "resources" / "kestrel.png").exists():
    icon = str(PKG / "resources" / "kestrel.png")

ONEFILE = os.environ.get("MITTENS_ONEFILE") == "1"

exe = EXE(                                     # noqa: F821
    pyz,
    a.scripts,
    *([a.binaries, a.zipfiles, a.datas] if ONEFILE else [[]]),
    exclude_binaries=not ONEFILE,
    name="Mittens and Pence",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                                 # UPX trips antivirus heuristics
    console=False,                             # no terminal window on Windows
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
)

coll = None
if not ONEFILE:
    coll = COLLECT(                            # noqa: F821
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="Mittens and Pence",
    )

if sys.platform == "darwin":
    app = BUNDLE(                              # noqa: F821
        coll or exe,
        name="Mittens and Pence.app",
        icon=icon,
        bundle_identifier="uk.co.mittensandpence.finance",
        info_plist={
            # Read from config, never typed here. A hard-coded version in the bundle
            # is invisible until somebody checks Get Info on a build that is three
            # releases old and is told it is 1.0.0 — and by then the same wrong
            # number is on every copy that has ever gone out.
            "CFBundleShortVersionString": _cfg.APP_VERSION,
            "CFBundleVersion": _cfg.APP_VERSION,
            "NSHighResolutionCapable": True,
            "LSApplicationCategoryType": "public.app-category.finance",
        },
    )
