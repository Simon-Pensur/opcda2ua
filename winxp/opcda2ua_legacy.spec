# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for OPC DA to OPC UA Bridge - Legacy Edition

Builds a directory-based distribution compatible with Windows XP / Server 2003.
Requires Python 2.7 32-bit + PyInstaller 3.6.

Uses --onedir mode because --onefile has a known issue with VC90 CRT manifest
extraction on XP ("Microsoft.VC90.CRT.manifest could not be extracted!").
"""

import sys
import os

spec_dir = os.path.dirname(os.path.abspath(SPEC))

block_cipher = None

a = Analysis(
    [os.path.join(spec_dir, 'opcda2ua_legacy.py')],
    pathex=[spec_dir],
    binaries=[],
    datas=[],
    hiddenimports=[
        'opcua',
        'opcua.server',
        'opcua.server.server',
        'opcua.common',
        'opcua.ua',
        'opcua.ua.uatypes',
        'opcua.ua.status_codes',
        'opcua.ua.object_ids',
        'opcua.ua.attribute_ids',
        'opcua.crypto',
        'OpenOPC',
        'win32com',
        'win32com.client',
        'win32com.server',
        'win32com.server.util',
        'pythoncom',
        'pywintypes',
        'win32api',
        'enum34',
        'trollius',
        'concurrent.futures',
        # Ensure os.path resolution works in frozen exe
        'ntpath',
        'genericpath',
        'stat',
    ],
    hookspath=[],
    runtime_hooks=[os.path.join(spec_dir, 'rthook_ospath.py')],
    excludes=[
        'tkinter',
        'unittest',
        'pydoc',
        'doctest',
        'difflib',
        # Exclude pywin32 GUI/MFC components (win32ui triggers the
        # pywin32 installer dialog on first run)
        'win32ui',
        'Pythonwin',
        'pywin32_postinstall',
        # Exclude 'future' package - it's a build-time dependency of pefile,
        # not needed at runtime. Its import hooks cause "No module named path"
        # errors inside frozen PyInstaller executables.
        'future',
        'past',
        'libfuturize',
        'libpasteurize',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Filter out MFC DLLs and win32ui (GUI components not needed)
_mfc_exclude = {'mfc90.dll', 'mfc90u.dll', 'mfcm90.dll', 'mfcm90u.dll',
                'win32ui.pyd', 'Microsoft.VC90.MFC.manifest'}
a.binaries = [b for b in a.binaries if os.path.basename(b[0]).lower() not in
              {x.lower() for x in _mfc_exclude}]
a.datas = [d for d in a.datas if os.path.basename(d[0]).lower() not in
           {x.lower() for x in _mfc_exclude}]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='opcda2ua_winxp',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='opcda2ua_winxp',
)
