block_cipher = None

a = Analysis(
    ['../src/monitor.py'],
    pathex=['E:\\Erez\\OneDrive\\Documents\\- My Projects -\\NetSpeedTray'],
    binaries=[],
    datas=[('../assets/*', 'assets')],
    hiddenimports=[
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
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='NetSpeedTray',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon='../assets/NetSpeedTray.ico',
    version='version_info.txt',
)