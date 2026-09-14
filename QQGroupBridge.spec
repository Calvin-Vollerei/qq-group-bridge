# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onedir / 窗口模式）。

打包策略与理由：

* **onedir 而非 onefile** —— 启动快、**杀软误报率显著更低**；
  onefile 每次启动都要解压到临时目录，行为更像恶意软件。
* **windowed（console=False）** —— 不弹控制台黑窗；自检用 ``--selftest``
  时由调用方通过重定向读取输出（见 自检.bat）。
* **不打包任何凭据 / 配置 / NapCat 二进制** —— 分发包保持"零凭据"，
  QQ 组件由用户在界面里按向导安装。
* **排除开发与测试代码** —— ``tests`` / ``qgb.dev`` 不进发布包。
* **PySide6 只带必需模块** —— 新界面（``qgb/qt``）只用到 QtCore/QtGui/QtWidgets。
  PySide6 安装目录近 190MB，其中 WebEngine/Qt3D/QtMultimedia 等占了绝大部分；
  不排除的话发布包会从 52MB 涨到 200MB+。这里按模块名精确排除，
  并保留 Qt 的 windows 平台插件（缺了它界面根本起不来）。

构建入口：``scripts/build.ps1``（它会先跑测试与扫密再调用本文件）。
"""

from pathlib import Path

ROOT = Path(SPECPATH).resolve()  # noqa: F821  (SPECPATH 由 PyInstaller 注入)

# ---------------------------------------------------------------- 隐式依赖

hiddenimports = [
    # Pillow 与 tkinter 配合显示 QR 图时必需（旧 Tk 界面仍在）
    "PIL._tkinter_finder",
    "PIL.Image",
    "PIL.ImageTk",
    # PySide6 新界面用到的三个模块（显式列出，避免被分析漏掉）
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    # ⚠️ 新界面的页面模块是**动态导入**的（pages.py 里用
    #    importlib.import_module 懒加载），PyInstaller 的静态分析发现不了它们。
    #    不显式列出来，打包后会报 ModuleNotFoundError: No module named
    #    'qgb.qt.page_groups' —— 实测踩到过（源码跑没问题、exe 一开就崩）。
    "qgb.qt.app",
    "qgb.qt.shell",
    "qgb.qt.pages",
    "qgb.qt.widgets",
    "qgb.qt.themes",
    "qgb.qt.blur",
    "qgb.qt.glass",
    "qgb.qt.page_groups",
    "qgb.qt.page_qq",
    "qgb.qt.page_netdisk",
    "qgb.qt.page_advanced",
]

# ---------------------------------------------------------------- 排除项

excludes = [
    # 开发与测试（绝不进发布包）
    "tests",
    "qgb.dev",
    "unittest",
    "doctest",
    "pdb",
    "pytest",
    "_pytest",
    # 用不到的重型库：显著减小体积
    "numpy",
    "matplotlib",
    "scipy",
    "pandas",
    "IPython",
    "jupyter",
    # 打包/构建工具自身
    "setuptools",
    "pip",
    "wheel",
    "PyInstaller",
    # ==== Qt 里用不到的重型模块（不排除会让包涨到 200MB+）====
    # 新界面只用 QtCore/QtGui/QtWidgets，以下全部排除。
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQuickControls2",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtGraphs",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtLocation",
    "PySide6.QtSerialPort",
    "PySide6.QtSerialBus",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtUiTools",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtSvgWidgets",
    "PySide6.QtSpatialAudio",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtStateMachine",
    "PySide6.QtTextToSpeech",
    # 注意：shiboken6 是 PySide6 的必需运行时，**不能**排除
    # 其他无关项
    "test",
    "lib2to3",
    "pydoc_data",
    "sqlite3.test",
]

# ---------------------------------------------------------------- 构建

a = Analysis(  # noqa: F821
    ["run_bridge.py"],
    pathex=[str(ROOT)],
    binaries=[],
    # 应用图标：必须显式打进去，否则打包后 qgb/assets 不存在，
    # 窗口/托盘图标就是空的（实测踩到：用户问"图标到哪去了"）。
    datas=[
        ("qgb/assets/icon.ico", "qgb/assets"),
        ("qgb/assets/icon.png", "qgb/assets"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="QQGroupBridge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,           # UPX 会显著提高杀软误报率，刻意关闭
    console=False,       # 窗口模式：不弹黑窗
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # exe 自身的图标（资源管理器/任务栏/开始菜单看到的就是它）
    icon="qgb/assets/icon.ico",
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="QQGroupBridge",
)
