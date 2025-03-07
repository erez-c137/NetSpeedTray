# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['NetSpeedTray.py'],
    pathex=[],
    binaries=[],
    datas=[('NetSpeedTray.ico', '.')],
    hiddenimports=[
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.QtWidgets',
        'psutil',
        'pywin32',
        'win32com.shell.shell',
        'matplotlib',              # Broad import for safety
        'matplotlib.pyplot',       # Explicitly include pyplot
        'matplotlib.backends.backend_qtagg',
        'numpy',
        'signal'
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],                   # Remove matplotlib.pyplot from excludes
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
    icon='NetSpeedTray.ico',
    version='version_info.txt',
)
