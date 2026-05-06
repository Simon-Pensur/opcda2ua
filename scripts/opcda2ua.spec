# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for OPC DA → OPC UA Bridge (Windows 10, asyncua).
Build with Python 3.11 32-bit (for in-proc COM compatibility with gbda_aut_32.dll):
    pyinstaller --clean opcda2ua.spec
Output: dist\\opcda2ua.exe  (rename to opcda2ua_win10.exe for distribution)
"""

import os

spec_dir = os.path.dirname(os.path.abspath(SPEC))
project_root = os.path.dirname(spec_dir)

block_cipher = None

a = Analysis(
    [os.path.join(project_root, 'openopc2', 'ua_server.py')],
    pathex=[project_root, os.path.join(project_root, 'openopc2')],
    binaries=[],
    datas=[],
    hiddenimports=[
        # asyncua and its address-space data
        'asyncua',
        'asyncua.server',
        'asyncua.server.server',
        'asyncua.server.address_space',
        'asyncua.common',
        'asyncua.ua',
        'asyncua.ua.uatypes',
        'asyncua.ua.status_codes',
        'asyncua.ua.object_ids',
        'asyncua.ua.attribute_ids',
        'asyncua.crypto',
        'cryptography',
        'cryptography.hazmat.bindings.openssl.binding',
        'aiosqlite',
        # pywin32 / COM
        'win32com',
        'win32com.client',
        'win32com.client.gencache',
        'win32com.server',
        'win32com.server.util',
        'pythoncom',
        'pywintypes',
        'win32api',
        'win32event',
        'win32timezone',
        # rich logging
        'rich',
        'rich.logging',
        # stdlib pieces some hooks miss
        'json',
        'ntpath',
        'genericpath',
        'stat',
        'concurrent.futures',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'unittest',
        'pydoc',
        'doctest',
        'difflib',
        'win32ui',
        'Pythonwin',
        'pywin32_postinstall',
    ],
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
    name='opcda2ua',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
