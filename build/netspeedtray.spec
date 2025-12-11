# NetSpeedTray.spec
from PyInstaller.utils.hooks import collect_all

block_cipher = None

# --- FORCE COLLECT PANDAS ---
# This grabs all hidden imports, data files, and binaries for pandas
pandas_datas, pandas_binaries, pandas_hiddenimports = collect_all('pandas')

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

# Combine them
all_hidden_imports = my_hidden_imports + pandas_hiddenimports

a = Analysis(
    ['..\\src\\monitor.py'],
    pathex=[],
    binaries=pandas_binaries, # Include pandas binaries
    datas=[
        ('..\\assets', 'assets'),
        ('..\\src\\netspeedtray\\constants\\locales', 'netspeedtray/constants/locales')
    ] + pandas_datas, # Include pandas data
    hiddenimports=all_hidden_imports, # Include pandas hidden imports
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
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