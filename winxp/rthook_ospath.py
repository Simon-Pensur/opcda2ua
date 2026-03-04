# -*- coding: utf-8 -*-
"""
PyInstaller runtime hook: fix 'os.path' import in frozen exe.

Problem: PyInstaller 3.6 + Python 2.7 + pywin32: when win32com gencache
generates type library wrappers at runtime, the import chain
pywintypes -> os -> 'from os.path import ...' fails with
"No module named path" because PyInstaller's frozen importer doesn't
properly register os.path as an importable submodule.

Fix: install a sys.meta_path hook that resolves 'os.path' to ntpath,
and ensure sys.modules has the correct mapping at startup.
"""
import sys
import imp


class _OSPathImportFixer(object):
    """Meta-path finder that resolves 'os.path' to 'ntpath' on Windows.

    In normal Python, 'os.path' is added to sys.modules during os.py
    initialization. In PyInstaller frozen builds, this mapping can be
    lost when modules are imported through non-standard paths (e.g.
    COM type library wrappers generated at runtime by win32com.gencache).
    """

    def find_module(self, fullname, path=None):
        # Intercept attempts to import 'os.path' as a submodule
        if fullname == 'os.path':
            return self
        return None

    def load_module(self, fullname):
        if fullname in sys.modules:
            return sys.modules[fullname]
        # os.path is ntpath on Windows
        import ntpath
        sys.modules[fullname] = ntpath
        return ntpath


# Install the hook FIRST, before anything else imports
sys.meta_path.insert(0, _OSPathImportFixer())

# Also pre-populate sys.modules
import ntpath
import os
sys.modules['os.path'] = ntpath
sys.modules['ntpath'] = ntpath
