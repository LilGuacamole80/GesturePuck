# GesturePuck.spec
from PyInstaller.utils.hooks import collect_all

# Collect all bleak and pynput dependencies automatically
bleak_datas, bleak_binaries, bleak_hiddenimports = collect_all('bleak')
pynput_datas, pynput_binaries, pynput_hiddenimports = collect_all('pynput')

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[*bleak_binaries, *pynput_binaries],
    datas=[
        *bleak_datas,
        *pynput_datas,
    ],
    hiddenimports=[
        *bleak_hiddenimports,
        *pynput_hiddenimports,
        # macOS specific
        'AppKit',
        'Foundation',
        'objc',
        # your modules
        'engine.mappings',
        'engine.macro_runner',
        'engine.bluetooth_spp',
        'engine.active_app',
        'engine.controller',  # added
        'ui.tkinter_ui',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'mappings.json',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='GesturePuck',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='GesturePuck',
)

app = BUNDLE(
    coll,
    name='GesturePuck.app',
    # icon='assets/icon.icns',  # uncomment when you have an icon
    bundle_identifier='com.gesturepuck.app',
    info_plist={
        'NSBluetoothAlwaysUsageDescription': 'GesturePuck needs Bluetooth to connect to your device.',
        'NSBluetoothPeripheralUsageDescription': 'GesturePuck needs Bluetooth to connect to your device.',
        'NSAppleEventsUsageDescription': 'GesturePuck needs this to detect which app is active.',
        'NSAccessibilityUsageDescription': 'GesturePuck needs Accessibility access to simulate key presses.',
        'LSUIElement': False,  # show in dock so macOS permission prompts work correctly
    },
)
