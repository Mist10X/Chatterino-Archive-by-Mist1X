# Third-party components

- Panel and History Tools code: MIT, see ../LICENSE and ../plugin/LICENSE.
- Python 3.12.14: PSF license and included notices, Python-LICENSE.txt. Source: https://www.python.org/downloads/source/
- PyInstaller 6.22.2 bootloader: GPL with the bootloader exception; PyInstaller-LICENSE.txt. Source: https://github.com/pyinstaller/pyinstaller/tree/v6.22.2
- PySide6 Essentials and Shiboken 6.11.2: LGPL 3.0 option. Upstream: https://code.qt.io/cgit/pyside/pyside-setup.git/tree/?h=v6.11.2 ; packages: https://pypi.org/project/PySide6-Essentials/6.11.2/ and https://pypi.org/project/shiboken6/6.11.2/
- Qt 6.11.2 Core, Gui, Widgets, Network, Svg and the platform/image/style plugins included by the standard PyInstaller Qt hooks: unmodified upstream dynamic libraries, LGPL 3.0 option. LGPL-3.0-only.txt and the referenced GPL-3.0-only.txt are included. Qt notices: https://doc.qt.io/qt-6/licensing.html ; source: https://code.qt.io/cgit/qt/qtbase.git/tree/?h=v6.11.2 and https://code.qt.io/cgit/qt/qtsvg.git/tree/?h=v6.11.2
- Qt's third-party components retain their own licenses and copyright notices: https://doc.qt.io/qt-6/licenses-used-in-qt.html . Qt for Python attribution: https://doc.qt.io/qtforpython-6/licenses.html
- Archive Montserrat (static 400/600 derived with renamed family): SIL OFL 1.1, Montserrat-OFL.txt. Original source: https://github.com/google/fonts/tree/main/ofl/montserrat . No global font installation.

Qt/PySide6 are not statically linked into our executable. The onedir package leaves these libraries replaceable in _internal; no runtime signature or checksum enforcement prevents a compatible replacement. Application sources and rebuilding instructions are available at https://github.com/Mist10X/Chatterino-Archive-by-Mist1X/tree/main/source . No additional restriction on modification or debugging of LGPL components is imposed by this application.


## 7TV network service

This application accesses the public 7TV v3 REST API at https://7tv.io/v3 and downloads selected emote files from https://cdn.7tv.app on the user's request through the archive viewer. SevenTV code is not bundled. Third-party emote images are not redistributed in this package and retain their respective authors' rights. This integration is not affiliated with or endorsed by 7TV.

Primary format references: https://github.com/SevenTV/SevenTV/tree/main/apps/api/src/http/v3/rest and https://github.com/SevenTV/SevenTV/blob/main/shared/src/old_types/mod.rs .
