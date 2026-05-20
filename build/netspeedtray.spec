# NetSpeedTray.spec
from PyInstaller.utils.hooks import collect_all

block_cipher = None

# Define your manual hidden imports
my_hidden_imports = [
    'PyQt6.QtCore',
    'PyQt6.QtGui',
    'PyQt6.QtWidgets',
    'psutil',
    'win32api',
    'win32com.shell.shell',
    'matplotlib',
    'matplotlib.pyplot',
    'matplotlib.backends.backend_qtagg',
    'numpy',
    'signal',
    'wmi',
]

# Modules that get pulled in transitively but aren't used by NetSpeedTray.
# Each line is a deliberate exclusion — adding more is fine, but verify the
# resulting EXE still launches and that Graph + App Activity render correctly
# before merging changes here.
my_excludes = [
    # We already had this one.
    'pandas',

    # Tk: app uses PyQt6, not Tk. matplotlib's default backend probe touches
    # this so excluding the Python module forces the QtAgg path we want.
    'tkinter',
    '_tkinter',

    # Developer-only tooling that PyInstaller picks up via setuptools/pip.
    'lib2to3',
    'pydoc_data',
    'IPython',
    'jedi',
    'notebook',

    # Matplotlib backends we never use. App explicitly calls
    # matplotlib.use('QtAgg') in monitor.py, so non-Qt backends are dead weight.
    'matplotlib.backends.backend_wx',
    'matplotlib.backends.backend_wxagg',
    'matplotlib.backends.backend_wxcairo',
    'matplotlib.backends.backend_gtk3',
    'matplotlib.backends.backend_gtk3agg',
    'matplotlib.backends.backend_gtk3cairo',
    'matplotlib.backends.backend_gtk4',
    'matplotlib.backends.backend_gtk4agg',
    'matplotlib.backends.backend_gtk4cairo',
    'matplotlib.backends.backend_tkagg',
    'matplotlib.backends.backend_tkcairo',
    'matplotlib.backends.backend_webagg',
    'matplotlib.backends.backend_webagg_core',
    'matplotlib.backends.backend_nbagg',
    'matplotlib.backends.backend_macosx',

    # 3D / geographic plotting toolkits — app only does 2D line charts.
    'mpl_toolkits.mplot3d',
    'mpl_toolkits.basemap',
]

a = Analysis(
    ['..\\src\\monitor.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('..\\assets', 'assets'),
        ('..\\src\\netspeedtray\\constants\\locales', 'netspeedtray/constants/locales')
    ],
    hiddenimports=my_hidden_imports,
    hookspath=[],
    runtime_hooks=[],
    excludes=my_excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    a.binaries,
    a.datas,
    name='NetSpeedTray',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='..\\assets\\NetSpeedTray.ico',
    version='version_info.txt'
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='NetSpeedTray'
)