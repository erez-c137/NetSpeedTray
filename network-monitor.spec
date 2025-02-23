# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

# Collect minimal matplotlib data (only fonts and stylelib for qtagg backend)
matplotlib_data_path = 'C:\\Python311\\Lib\\site-packages\\matplotlib\\mpl-data'
matplotlib_datas = [
    (os.path.join(matplotlib_data_path, 'fonts'), 'matplotlib\\fonts'),
    (os.path.join(matplotlib_data_path, 'stylelib'), 'matplotlib\\stylelib')
]

# Collect only essential PyQt6 plugins (platforms for Windows GUI, imageformats for basic images)
pyqt6_plugins = [
    ('C:\\Python311\\Lib\\site-packages\\PyQt6\\Qt6\\plugins\\platforms', 'PyQt6\\Qt6\\plugins\\platforms'),
    ('C:\\Python311\\Lib\\site-packages\\PyQt6\\Qt6\\plugins\\imageformats', 'PyQt6\\Qt6\\plugins\\imageformats')
]

a = Analysis(
    ['network-monitor.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('NetSpeedTray.ico', '.'),
    ] + matplotlib_datas + pyqt6_plugins,
    hiddenimports=[
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.QtWidgets',
        'matplotlib.backends.backend_qtagg',
        'pywin32',
        'psutil',
        'numpy'  # Added numpy to hiddenimports to resolve matplotlib's dependency
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'PyQt6.QtMultimedia', 'PyQt6.QtQuick', 'PyQt6.Qt3D', 'PyQt6.QtSql'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='NetSpeedTray',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[
        'Qt6Core.dll',
        'Qt6Gui.dll',
        'Qt6Widgets.dll',
        'msvcp140.dll',
        'msvcp140_1.dll',
        'msvcp140_2.dll',
        'vcruntime140.dll',
        'vcruntime140_1.dll',
        'qwindows.dll',
        'qgif.dll',
        'qjpeg.dll',
        'qicns.dll',
        'qico.dll',
        'qpdf.dll',
        'qsvg.dll',
        'qtga.dll',
        'qtiff.dll',
        'qwbmp.dll',
        'qwebp.dll',
        'qminimal.dll',
        'qoffscreen.dll',
        'msvcp140-263139962577ecda4cd9469ca360a746.dll',
        'qmodernwindowsstyle.dll',
        'qtuiotouchplugin.dll',
        'qsvgicon.dll',
        'Qt6Pdf.dll',
        'Qt6Svg.dll',
        'Qt6Network.dll'
    ],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='NetSpeedTray.ico'
)