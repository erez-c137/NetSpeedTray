# NetSpeedTray.spec
import os

block_cipher = None

# Local UPX install (auto-downloaded by build.bat into build/tools/upx-<ver>/).
# PyInstaller checks this directory; if upx.exe isn't there, it silently
# proceeds without compression rather than failing the build.
_spec_dir = os.path.dirname(os.path.abspath(SPEC))
_upx_candidates = [
    os.path.join(_spec_dir, 'tools', 'upx-5.0.2-win64'),
    os.path.join(_spec_dir, 'tools'),
]
upx_dir = next((p for p in _upx_candidates if os.path.exists(os.path.join(p, 'upx.exe'))), None)
if upx_dir:
    print(f'[netspeedtray.spec] UPX found at: {upx_dir}')
else:
    print('[netspeedtray.spec] UPX not found in build/tools/ — binaries will not be compressed.')

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

    # --- PyQt6 modules we never import (verified by grep across src/) ---
    # UpdateChecker uses stdlib urllib, not QtNetwork. Excluding it drops
    # Qt6Network.dll + libcrypto-3.dll + libssl-3.dll (~7 MB uncompressed).
    'PyQt6.QtNetwork',
    # PDF rendering not used. Drops Qt6Pdf.dll (~5 MB).
    'PyQt6.QtPdf',
    'PyQt6.QtPdfWidgets',
    # We render via QPainter and matplotlib. No Quick/QML/3D anywhere.
    'PyQt6.QtQml',
    'PyQt6.QtQuick',
    'PyQt6.QtQuickWidgets',
    'PyQt6.QtQuick3D',
    'PyQt6.QtMultimedia',
    'PyQt6.QtMultimediaWidgets',
    'PyQt6.QtWebEngine',
    'PyQt6.QtWebEngineCore',
    'PyQt6.QtWebEngineWidgets',
    'PyQt6.QtWebChannel',
    'PyQt6.QtSql',
    'PyQt6.QtDesigner',
    'PyQt6.QtCharts',
    'PyQt6.QtTest',
    'PyQt6.QtPositioning',
    'PyQt6.QtLocation',
    'PyQt6.QtSensors',
    'PyQt6.QtBluetooth',
    'PyQt6.QtNfc',
    'PyQt6.QtSerialPort',
    'PyQt6.QtSerialBus',
    'PyQt6.QtRemoteObjects',
    'PyQt6.QtDBus',
    'PyQt6.QtHelp',
    'PyQt6.QtOpenGL',
    'PyQt6.QtOpenGLWidgets',

    # Pythonwin is the win32-based MFC IDE shell shipped with pywin32.
    # We use win32api/event/gui/con — none need MFC. Drops mfc140u.dll (~5 MB)
    # plus win32ui.pyd (~1 MB).
    'pythonwin',
    'win32ui',
    'win32uiole',

    # Windows Event Log bindings. Pulled in transitively (the only stdlib
    # consumer is logging.handlers.NTEventLogHandler, which we don't use — we
    # log to a file). Nothing in src/ imports win32evtlog. Excluding it drops
    # win32\win32evtlog.pyd, which some heuristic AVs (e.g. Webroot) flag as a
    # false positive on the frozen build (#135). Verify the EXE still launches
    # and file logging works after changing this.
    'win32evtlog',
    'win32evtlogutil',

    # PIL plugins for image formats we never load. matplotlib's PIL usage
    # is only for PNG figure backing — these niche codecs are dead weight.
    # Drops PIL\_avif.cp311.pyd (~7.5 MB uncompressed) plus minor formats.
    'PIL.AvifImagePlugin',
    'PIL.HeifImagePlugin',
    'PIL.JpegPresets',
    'PIL.PsdImagePlugin',
    'PIL.SgiImagePlugin',
    'PIL.SunImagePlugin',
    'PIL.TgaImagePlugin',
    'PIL.WmfImagePlugin',
    'PIL.WebPImagePlugin',
    'PIL.XbmImagePlugin',
    'PIL.XpmImagePlugin',
]


def _drop_unused_qt_artifacts(items):
    """Remove Qt artifacts whose Python bindings we've excluded.

    PyInstaller's PyQt6 hook always bundles the entire `PyQt6\\Qt6\\bin\\`
    folder and `Qt6/plugins/` tree, even when only QtCore/QtGui/QtWidgets are
    imported. This filter post-processes the binaries+datas list to drop the
    DLLs and plugins backing modules that don't appear in any import path:

      * Qt6Pdf.dll, Qt6Quick*.dll, Qt6Qml*.dll, Qt6Multimedia*.dll,
        Qt6Network.dll + OpenSSL DLLs, Qt6Designer.dll, Qt6Sql.dll
      * Plugins under sqldrivers/, sceneparsers/, assetimporters/,
        renderers/, qmlls/, multimedia/, position/, sensors/, webview/,
        designer/, scxmldatamodel/
      * QML asset trees we'll never touch (~5 MB of QtQuick.Controls)

    These cuts are only safe because the corresponding `PyQt6.Qt*` module
    is in `my_excludes` above — i.e. nothing in our code paths will ever
    `dlopen` them. Adding a new PyQt6 import without removing the matching
    line here will produce ImportError at runtime.
    """
    drop_dll_names = {
        # PDF
        'qt6pdf.dll',
        # Quick/QML/3D
        'qt6qml.dll', 'qt6qmlcore.dll', 'qt6qmlmodels.dll', 'qt6qmlworkerscript.dll',
        'qt6qmlmeta.dll',
        'qt6quick.dll', 'qt6quickcontrols2.dll', 'qt6quickcontrols2basic.dll',
        'qt6quickcontrols2basicstyleimpl.dll', 'qt6quickcontrols2fusion.dll',
        'qt6quickcontrols2fusionstyleimpl.dll', 'qt6quickcontrols2imagine.dll',
        'qt6quickcontrols2imaginestyleimpl.dll', 'qt6quickcontrols2impl.dll',
        'qt6quickcontrols2material.dll', 'qt6quickcontrols2materialstyleimpl.dll',
        'qt6quickcontrols2universal.dll', 'qt6quickcontrols2universalstyleimpl.dll',
        'qt6quickdialogs2.dll', 'qt6quickdialogs2quickimpl.dll',
        'qt6quickdialogs2utils.dll', 'qt6quicklayouts.dll', 'qt6quickparticles.dll',
        'qt6quickshapes.dll', 'qt6quicktemplates2.dll', 'qt6quicktest.dll',
        'qt6quickwidgets.dll', 'qt6quickeffects.dll',
        'qt6quick3d.dll', 'qt6quick3dassetimport.dll', 'qt6quick3dassetutils.dll',
        'qt6quick3deffects.dll', 'qt6quick3dglslparser.dll', 'qt6quick3dhelpers.dll',
        'qt6quick3dhelpersimpl.dll', 'qt6quick3diblbaker.dll',
        'qt6quick3dparticleeffects.dll', 'qt6quick3dparticles.dll',
        'qt6quick3dphysics.dll', 'qt6quick3dphysicshelpers.dll',
        'qt6quick3druntimerender.dll', 'qt6quick3dspatialaudio.dll',
        'qt6quick3dutils.dll', 'qt6quick3dxr.dll',
        'qt6shadertools.dll',
        # Multimedia
        'qt6multimedia.dll', 'qt6multimediawidgets.dll', 'qt6multimediaquick.dll',
        'qt6spatialaudio.dll', 'avcodec-61.dll', 'avformat-61.dll', 'avutil-59.dll',
        'swresample-5.dll', 'swscale-8.dll',
        # Qt's networking stack. We exclude the PyQt6.QtNetwork *module*, so
        # qt6network.dll is dead weight and safe to drop.
        #
        # DO NOT add libcrypto-3.dll / libssl-3.dll here. Those are OpenSSL, and
        # on Windows Python's own `ssl` module (urllib -> _ssl.pyd / _hashlib.pyd)
        # is backed by OpenSSL, NOT SChannel. This filter matches by *basename*,
        # so listing them strips Python's copy too — which is exactly what broke
        # all HTTPS in v1.3.2 ("Could not check for updates"). Keep them bundled.
        'qt6network.dll',
        # Designer
        'qt6designer.dll', 'qt6designercomponents.dll', 'qt6uitools.dll',
        # SQL
        'qt6sql.dll',
        # Other unused
        'qt6charts.dll', 'qt6chartsqml.dll',
        'qt6test.dll',
        'qt6positioning.dll', 'qt6positioningquick.dll',
        'qt6location.dll',
        'qt6sensors.dll', 'qt6sensorsquick.dll',
        'qt6bluetooth.dll', 'qt6nfc.dll',
        'qt6serialport.dll', 'qt6serialbus.dll',
        'qt6remoteobjects.dll', 'qt6remoteobjectsqml.dll',
        'qt6webengine.dll', 'qt6webengineCore.dll', 'qt6webenginewidgets.dll',
        'qt6webchannel.dll', 'qt6webchannelquick.dll',
        'qt6help.dll',
        'qt6opengl.dll', 'qt6openglwidgets.dll',
        'qt6dbus.dll',
        'qt6scxml.dll', 'qt6scxmlqml.dll', 'qt6statemachine.dll',
        'qt6texttospeech.dll',
        'qt6dataVisualization.dll', 'qt6datavisualizationqml.dll',
        'qt6virtualkeyboard.dll',
        'qt6httpserver.dll', 'qt6grpc.dll', 'qt6protobuf.dll', 'qt6protobufquick.dll',
        'qt6concurrent.dll', 'qt6xml.dll',
    }
    drop_plugin_dirs = {
        'sqldrivers', 'sceneparsers', 'assetimporters', 'renderers', 'qmlls',
        'multimedia', 'position', 'sensors', 'webview', 'designer',
        'scxmldatamodel', 'qtwebengine',
    }
    drop_qml_modules = {
        # The matched suffix in the artifact path. ~5 MB of asset graphs.
        'qml\\QtQuick', 'qml\\Qt3D', 'qml\\QtMultimedia', 'qml\\QtCharts',
        'qml\\QtPdf', 'qml\\QtWebEngine', 'qml\\QtWebChannel',
    }
    # Software OpenGL fallback (~20 MB uncompressed). Qt prefers hardware
    # acceleration; on systems with broken GPU drivers Qt can fall back to
    # this. We keep it — removing it is the highest-risk cut.
    # If you do want to drop it, add 'opengl32sw.dll' to drop_dll_names.

    kept = []
    dropped_count = 0
    dropped_bytes = 0
    for item in items:
        dest, src, kind = item[0], item[1], item[2]
        lower_dest = dest.lower().replace('/', '\\')
        basename = os.path.basename(lower_dest)
        if basename in drop_dll_names:
            dropped_count += 1
            try:
                dropped_bytes += os.path.getsize(src) if src else 0
            except OSError:
                pass
            continue
        if any(f'\\plugins\\{d}\\' in lower_dest for d in drop_plugin_dirs):
            dropped_count += 1
            continue
        if any(m.lower() in lower_dest for m in drop_qml_modules):
            dropped_count += 1
            continue
        kept.append(item)
    print(
        f'[netspeedtray.spec] Dropped {dropped_count} unused Qt artifacts '
        f'(~{dropped_bytes/1024/1024:.1f} MB on-disk).'
    )
    return kept


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

# Strip Qt DLLs/plugins for modules we excluded above. PyInstaller's PyQt6
# hook bundles them unconditionally; we have to filter post-analysis.
a.binaries = _drop_unused_qt_artifacts(a.binaries)
a.datas = _drop_unused_qt_artifacts(a.datas)

# Regression guard: v1.3.2 silently shipped without OpenSSL and broke every
# HTTPS call in the frozen app (the update checker, most visibly). Fail the
# build loudly rather than ship a bundle whose `ssl` module can't load.
_required_runtime_libs = ('libcrypto-3', 'libssl-3', '_ssl', '_hashlib')
_bundled_basenames = {os.path.basename(b[0]).lower() for b in a.binaries}
for _need in _required_runtime_libs:
    if not any(_need in name for name in _bundled_basenames):
        raise SystemExit(
            f"[netspeedtray.spec] FATAL: '{_need}' is missing from the bundle. "
            f"Python's ssl module will fail at runtime (HTTPS + update checker "
            f"broken). Check _drop_unused_qt_artifacts() and `my_excludes`."
        )
print('[netspeedtray.spec] SSL runtime libs present: OpenSSL + _ssl/_hashlib OK.')

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# UPX compression: roughly halves DLL sizes. Skip the items below — they
# are either already compressed, signed by Microsoft (UPX would invalidate
# the signature and trigger antivirus), or known to break when packed.
upx_exclude = [
    'vcruntime140.dll', 'vcruntime140_1.dll',
    'msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll',
    'python311.dll',          # Already heavily optimized; packing causes startup overhead.
    'qt6webenginecore.dll',   # Excluded above but defensive.
    'opengl32sw.dll',         # Mesa-derived, breaks if UPX-packed.
    # OpenSSL + Python's bindings. UPX-packing these is a known cause of SSL
    # load/handshake failures; keep them uncompressed so HTTPS keeps working.
    'libcrypto-3.dll', 'libssl-3.dll', '_ssl.pyd', '_hashlib.pyd',
    # numpy compiled core + its bundled OpenBLAS. UPX-packing these is a known
    # cause of "DLL load failed while importing _multiarray_umath" (#136).
    # upx_exclude entries are matched with pathlib.PurePath.match(), so globs
    # work — important because the OpenBLAS DLL name carries a version hash.
    '_multiarray_umath*.pyd', '_multiarray_tests*.pyd',
    'libscipy_openblas*.dll', 'libopenblas*.dll',
]
# Note: PyInstaller already auto-disables UPX for Qt plugins (e.g. qwindows.dll)
# and CFG-enabled binaries, so those need no manual entry here.

# --- Build mode: ONEDIR (release, default) vs ONEFILE (dev/test) ---
# ONEDIR (default): a small bootstrap exe + an `_internal/` folder that holds the
#   dependencies exactly once. Roughly HALF the size of onefile, and faster to
#   launch (no self-extraction to a temp dir on every run). This is what
#   build.bat ships (installer + portable zip) and what setup.iss expects.
# ONEFILE (NST_ONEFILE=1): a single self-contained exe - convenient for quick
#   testing or handing someone one portable file. Produced by build-exe-only.bat.
#
# The previous spec did BOTH at once: the EXE bundled a.binaries/a.datas
# (onefile) AND COLLECT re-copied them loose into the folder, so the release
# folder carried the whole payload twice (~2x on-disk). Splitting the two modes
# fixes that.
_onefile = bool(os.environ.get('NST_ONEFILE'))

if _onefile:
    print('[netspeedtray.spec] Build mode: ONEFILE (single self-contained exe).')
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
        upx=bool(upx_dir),
        upx_dir=upx_dir,
        upx_exclude=upx_exclude,
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon='..\\assets\\NetSpeedTray.ico',
        version='version_info.txt'
    )
else:
    print('[netspeedtray.spec] Build mode: ONEDIR (bootstrap exe + _internal/).')
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name='NetSpeedTray',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=bool(upx_dir),
        upx_dir=upx_dir,
        upx_exclude=upx_exclude,
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
        upx=bool(upx_dir),
        upx_exclude=upx_exclude,
        name='NetSpeedTray'
    )