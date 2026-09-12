# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import re
import runpy
from PyInstaller.utils.win32.versioninfo import VSVersionInfo,FixedFileInfo,StringFileInfo,StringTable,StringStruct,VarFileInfo,VarStruct

source = Path(SPECPATH)
brand = runpy.run_path(str(source / 'branding.py'))
metadata = VSVersionInfo(ffi=FixedFileInfo(filevers=brand['VERSION_TUPLE'],prodvers=brand['VERSION_TUPLE'],mask=0x3f,flags=0,OS=0x40004,fileType=1,subtype=0,date=(0,0)),kids=[
    StringFileInfo([StringTable('040904B0',[
        StringStruct('CompanyName',brand['AUTHOR']),
        StringStruct('FileDescription',brand['APP_NAME']),
        StringStruct('FileVersion',brand['VERSION']),
        StringStruct('InternalName',brand['EXE_STEM']),
        StringStruct('LegalCopyright',brand['COPYRIGHT']),
        StringStruct('OriginalFilename',brand['EXE_STEM']+'.exe'),
        StringStruct('ProductName',brand['APP_NAME']),
        StringStruct('ProductVersion',brand['VERSION']),
        StringStruct('Comments','Archive project by Mist1X')])]),
    VarFileInfo([VarStruct('Translation',[0x409,1200])])])
a = Analysis([str(source / 'panel.py')], pathex=[str(source)],
    binaries=[], datas=[(str(source / 'fonts'), 'fonts'), (str(source / 'release_config.json'), '.')], hiddenimports=[],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=['tkinter'], noarchive=False)
# Qt 6.11 needs Windows ICU with unversioned exports. Do not ship the build
# host's Python ICU 78: the same DLL name hides an incompatible export ABI.
a.binaries = [entry for entry in a.binaries
    if Path(entry[0]).name.lower() != 'icuuc.dll'
    and not re.fullmatch(r'icudt\d+\.dll', Path(entry[0]).name.lower())]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=brand['EXE_STEM'],
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False, argv_emulation=False,
    target_arch=None, codesign_identity=None, entitlements_file=None,
    icon=str(source / 'archive.ico'), version=metadata)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=brand['EXE_STEM'])
