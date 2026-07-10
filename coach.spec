# -*- mode: python ; coding: utf-8 -*-
# Spec PyInstaller pour "Coach d'échecs".
# Compile main.py + tous ses modules en un seul .exe autonome (mode --onefile).
#
# Utilisation locale (optionnel, le workflow GitHub Actions le fait automatiquement) :
#   pip install pyinstaller
#   pyinstaller coach.spec

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'chess',
        'mss',
        'cv2',
        'numpy',
        'tkinter',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='CoachEchecs',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,       # laisse une console visible: utile pour --calibrate / --learn
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
