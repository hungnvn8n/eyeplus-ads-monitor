# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — bundle Flask + templates + deps thành 1 file.

Build:
  Mac:     pyinstaller --clean fb_ad_local.spec
  Windows: pyinstaller --clean fb_ad_local.spec
"""

import sys
import os

block_cipher = None

# Data files cần đóng gói cùng exe
datas = [
    ('templates', 'templates'),    # Jinja templates
    ('static', 'static'),          # Ảnh sơ đồ quy tắc (rule_tram1/2.png)
    ('.env.example', '.'),         # Template config
    ('assets/logo.png', 'assets'), # Logo (dùng cho favicon/about)
]

# Icon platform-specific
if sys.platform == 'darwin':
    icon_file = 'assets/icon.icns'
elif sys.platform == 'win32':
    icon_file = 'assets/icon.ico'
else:
    icon_file = None

# Hidden imports — module Python không tự detect được
hiddenimports = [
    'flask', 'flask.cli', 'jinja2',
    'apscheduler', 'apscheduler.schedulers.background',
    'apscheduler.triggers.cron', 'apscheduler.triggers.interval',
    'requests', 'urllib3', 'charset_normalizer',
    'dotenv',
    'werkzeug', 'werkzeug.serving',
    'tzlocal', 'pytz',
    # pywebview platform backends
    'webview', 'webview.platforms.cocoa', 'webview.platforms.edgechromium',
    'webview.platforms.winforms', 'webview.platforms.gtk',
    'proxy_tools',
    '_bundled_secrets',  # FB tokens injected by CI (safe-fail if missing)
    'congthuc',          # Sổ công thức MKT — import động trong route nên phải khai báo
    'psycopg2',          # đọc kho dữ liệu cho phần chấm điểm công thức
]

a = Analysis(
    ['launcher.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Loại các module nặng không dùng
        'tkinter', 'matplotlib', 'numpy', 'pandas',
        'PIL', 'scipy', 'lxml',
        'tornado', 'IPython', 'pytest',
        'playwright', 'openpyxl',
        # Pywebview backends KHÔNG dùng cho platform này
        'webview.platforms.qt',   # PyQt5/6 — nặng
        'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
        'gi', 'gtk',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# Single-file executable
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='EyePlusAds',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # Ẩn console — native window thay thế
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_file,
)

# macOS .app bundle (chỉ Mac, build_mac.sh dùng)
if sys.platform == 'darwin':
    app = BUNDLE(
        exe,
        name='EyePlus Ads.app',
        icon='assets/icon.icns',
        bundle_identifier='vn.eyeplus.adsmonitor',
        info_plist={
            'NSHighResolutionCapable': 'True',
            'CFBundleShortVersionString': '1.0.0',
            'CFBundleVersion': '1',
            'LSEnvironment': {
                # Đảm bảo lang Unicode ổn định khi launch từ Finder
                'LANG': 'en_US.UTF-8',
                'LC_ALL': 'en_US.UTF-8',
            },
            # Cho phép pywebview launch từ Finder
            'NSHighResolutionCapable': True,
            'NSRequiresAquaSystemAppearance': False,
            'CFBundleIconFile': 'icon.icns',
        },
    )
