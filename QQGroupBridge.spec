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

构建入口：``scripts/build.ps1``（它会先跑测试与扫密再调用本文件）。
"""

from pathlib import Path

ROOT = Path(SPECPATH).resolve()  # noqa: F821  (SPECPATH 由 PyInstaller 注入)

# ---------------------------------------------------------------- 隐式依赖

hiddenimports = [
    # Pillow 与 tkinter 配合显示 QR 图时必需
    "PIL._tkinter_finder",
    "PIL.Image",
    "PIL.ImageTk",
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
    datas=[],
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
    icon=None,
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
