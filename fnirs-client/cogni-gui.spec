# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for NIRDuino fNIRS Client.

Usage:
    cd fnirs-client
    pyinstaller cogni-gui.spec

Produces:
    dist/NIRDuino          (macOS .app bundle)
    dist/NIRDuino.exe      (Windows executable)
    dist/NIRDuino          (Linux binary)
"""
import platform

block_cipher = None

a = Analysis(
    ['cogni-gui.py'],
    pathex=[],
    binaries=[],
    datas=[
        # ── Assets que la app carga en runtime ──
        ('fonts/JetBrainsMonoNL-Regular.ttf', 'fonts'),
    ],
    hiddenimports=[
        # bleak backends — PyInstaller no los detecta automáticamente
        'bleak',
        'bleak.backends',
        'bleak.backends.corebluetooth',     # macOS
        'bleak.backends.winrt',             # Windows
        'bleak.backends.bluezdbus',         # Linux
        # psycopg2
        'psycopg2',
        'psycopg2.extensions',
        'psycopg2.extras',
        # DearPyGui — renderer backend
        'dearpygui',
        'dearpygui.dearpygui',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Módulos no utilizados — reducir tamaño del ejecutable
        'tkinter',
        'matplotlib',
        'scipy',
        'pandas',
        'PIL',
        'cv2',
        'test',
        'unittest',
    ],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='NIRDuino',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,            # sin ventana de consola (app GUI)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,         # None = arquitectura actual; 'universal2' para macOS fat binary
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon.ico'] if platform.system() == 'Windows' else
         ['icon.icns'] if platform.system() == 'Darwin' else None,
)

# ── macOS: generar .app bundle ────────────────────────────────────────────────
if platform.system() == 'Darwin':
    app = BUNDLE(
        exe,
        name='NIRDuino.app',
        icon='icon.icns',
        bundle_identifier='com.cognisofia.nirduino',
        info_plist={
            'CFBundleDisplayName': 'NIRDuino',
            'CFBundleShortVersionString': '1.0.0',
            'CFBundleVersion': '1.0.0',
            'NSHighResolutionCapable': True,
            # ── Permisos BLE (obligatorio para que bleak funcione) ────
            'NSBluetoothAlwaysUsageDescription':
                'NIRDuino necesita Bluetooth para comunicarse con el dispositivo fNIRS.',
            'NSBluetoothPeripheralUsageDescription':
                'NIRDuino necesita Bluetooth para comunicarse con el dispositivo fNIRS.',
        },
    )
