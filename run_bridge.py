#!/usr/bin/env python3
"""QQ群文件搬运工 —— 程序入口。

三种用法::

    python run_bridge.py                           # 打开图形界面
    python run_bridge.py --selftest                # 无界面自检（打印到控制台）
    python run_bridge.py --selftest --out 报告.txt  # 自检结果写入文件
    python run_bridge.py --version                 # 版本信息

为什么要支持 ``--out``：打包后的程序是**窗口模式**（无控制台），
打包器会把 ``sys.stdout`` 置为不可用，此时直接 print 会抛异常。
因此自检统一走 :class:`Reporter`：能打印就打印，同时**一定**写文件，
``自检.bat`` 再把这个文件读出来给用户看。

打包后由 ``启动搬运工.bat`` 调用 ``QQGroupBridge.exe``。
"""

from __future__ import annotations

import argparse
import multiprocessing
import os
import sys
from pathlib import Path
from typing import TextIO


def _use_utf8_console() -> bool:
    """把控制台切到 UTF-8（本脚本的提示全是中文）。

    Linux/容器里非交互式 stdout 常是 cp1252，中文一 print 就抛
    ``UnicodeEncodeError``；Windows 控制台自 3.6 起走 UTF-8，所以只在
    别的平台复现。实现在 ``qgb.utils.force_utf8_stdio``，这里做一层
    容错包装：连 qgb 都导入不了时也要能继续跑（--help、自检等）。
    """
    _ensure_package_on_path()
    try:
        from qgb.utils import force_utf8_stdio  # noqa: PLC0415

        return force_utf8_stdio()
    except Exception:  # noqa: BLE001
        ok = True
        for name in ("stdout", "stderr"):
            stream = getattr(sys, name, None)
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure is None:
                ok = False
                continue
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                ok = False
        return ok


def _ensure_package_on_path() -> None:
    """支持从任意工作目录启动（打包后由 PyInstaller 处理）。"""
    if getattr(sys, "frozen", False):
        return
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))


class Reporter:
    """同时输出到控制台与文件（两者都不可用时也不崩）。"""

    def __init__(self, out_path: Path | None = None) -> None:
        self.lines: list[str] = []
        self.out_path = out_path
        self._stream: TextIO | None = None
        try:
            if sys.stdout is not None and sys.stdout.fileno() >= 0:
                self._stream = sys.stdout
        except (OSError, ValueError, AttributeError):
            self._stream = None

    def __call__(self, text: str = "") -> None:
        self.lines.append(text)
        if self._stream is not None:
            try:
                print(text, file=self._stream, flush=True)
            except (OSError, ValueError):
                self._stream = None

    def flush_to_file(self) -> Path | None:
        if not self.out_path:
            return None
        try:
            self.out_path.parent.mkdir(parents=True, exist_ok=True)
            self.out_path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")
            return self.out_path
        except OSError as exc:
            if self._stream is not None:
                print(f"[warn] 无法写入自检报告：{exc}", file=self._stream)
            return None


def _run_selftest(out_path: Path | None = None) -> int:
    """无界面自检：**不导入 tkinter**，因此在无桌面环境也能跑。"""
    from qgb.version import APP_NAME, __version__

    say = Reporter(out_path)
    problems: list[str] = []

    say("=" * 60)
    say(f"{APP_NAME} 自检  v{__version__}")
    say("=" * 60)
    say(f"平台      ：{sys.platform}  Python {sys.version.split()[0]}")
    say(f"打包运行  ：{'是' if getattr(sys, 'frozen', False) else '否（源码运行）'}")
    say(f"程序目录  ：{Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent}")

    # --- 数据目录 -------------------------------------------------
    try:
        from qgb.paths import data_dir_source, resolve_data_dir

        data_dir = resolve_data_dir()
        say(f"数据目录  ：{data_dir}")
        say(f"目录来源  ：{data_dir_source()}")
        probe = data_dir / ".selftest-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        say("可写性    ：✅ 可写")
    except Exception as exc:
        problems.append(f"数据目录不可用：{type(exc).__name__}: {exc}")
        say(f"可写性    ：❌ {type(exc).__name__}: {exc}")

    # --- 凭据后端 ------------------------------------------------
    try:
        from qgb import dpapi

        if dpapi.available():
            blob = dpapi.protect(b"selftest")
            ok = dpapi.unprotect(blob) == b"selftest"
            say(f"凭据加密  ：{'✅ Windows DPAPI 可用' if ok else '❌ 往返失败'}")
            if not ok:
                problems.append("DPAPI 往返校验失败")
        else:
            problems.append("当前系统不支持 DPAPI（仅支持 Windows 10/11）")
            say("凭据加密  ：❌ 不支持 DPAPI")
    except Exception as exc:
        problems.append(f"凭据加密不可用：{type(exc).__name__}: {exc}")
        say(f"凭据加密  ：❌ {type(exc).__name__}: {exc}")

    # --- 界面依赖 ------------------------------------------------
    try:
        import tkinter  # noqa: F401

        say("界面依赖  ：✅ tkinter 可用")
    except Exception as exc:
        problems.append(f"tkinter 不可用：{exc}")
        say(f"界面依赖  ：❌ {type(exc).__name__}: {exc}")

    try:
        import PIL  # noqa: F401  (Pillow)
        from PIL import ImageTk  # noqa: F401

        say("图像依赖  ：✅ Pillow 可用（用于显示登录二维码）")
    except Exception as exc:
        say(f"图像依赖  ：⚠ 缺少 Pillow（{type(exc).__name__}），二维码只能保存为文件")

    # --- 网络库 --------------------------------------------------
    try:
        import requests  # noqa: F401

        say(f"网络依赖  ：✅ requests {requests.__version__}")
    except Exception as exc:
        problems.append(f"requests 不可用：{exc}")
        say(f"网络依赖  ：❌ {type(exc).__name__}")

    # --- 应用层自检 ----------------------------------------------
    try:
        from qgb.controller import AppController

        controller = AppController()
        controller.load()
        health = controller.health()

        say("-" * 60)
        problems_text = "；".join(health["problems"]) if health["problems"] else "通过"
        say(f"配置校验  ：{'✅ ' if not health['problems'] else '⚠ '}{problems_text}")
        say(f"已配置群数：{health['groups']}")
        say(f"上传方式  ：{health['adapter']} → {health['target']}")
        creds = health.get("credentials", {})
        say(f"凭据后端  ：{creds.get('backend')}")
        say(f"凭据条数  ：{creds.get('count')}")
        napcat = health.get("napcat", {})
        say(
            "QQ 组件   ："
            + ("✅ 运行中" if napcat.get("running")
               else ("已安装未运行" if napcat.get("installed") else "未安装"))
        )
        say(f"日志目录  ：{health.get('log_dir')}")
        say(f"状态库    ：{health.get('state_db')}")

        hints: list[str] = []
        if health["groups"] == 0:
            hints.append("尚未配置群号 → 界面「群与规则」页添加")
        if not creds.get("count"):
            hints.append("尚未保存网盘凭据 → 界面「网盘与凭据」页完成授权")
        if not napcat.get("installed"):
            hints.append("尚未安装 QQ 组件 → 界面「QQ 登录」页按向导安装")

        controller.shutdown()
    except Exception as exc:
        problems.append(f"应用层自检失败：{type(exc).__name__}: {exc}")
        say(f"应用层    ：❌ {type(exc).__name__}: {exc}")
        hints = []

    say("=" * 60)
    if problems:
        say(f"❌ 自检发现 {len(problems)} 个阻塞问题：")
        for item in problems:
            say(f"   - {item}")
        say("")
        say("请把本报告（连同日志目录里的文件）提供给维护者。")
        written = say.flush_to_file()
        if written:
            say(f"[报告已写入] {written}")
        return 1

    say("✅ 自检通过：程序可以正常启动")
    if hints:
        say("")
        say("以下为待配置项（不影响启动）：")
        for item in hints:
            say(f"   · {item}")
    written = say.flush_to_file()
    if written:
        say(f"[报告已写入] {written}")
    return 0


def main(argv: list[str] | None = None) -> int:
    _use_utf8_console()
    parser = argparse.ArgumentParser(
        prog="QQGroupBridge",
        description="把指定 QQ 群的新文件自动抓取、过滤并上传到网盘",
    )
    parser.add_argument("--selftest", action="store_true", help="无界面自检并退出")
    parser.add_argument("--out", default="", help="把自检报告写入该文件（窗口模式必需）")
    parser.add_argument("--version", action="store_true", help="显示版本信息")
    parser.add_argument(
        "--ui", choices=("tk", "qt"), default="qt",
        help="选择界面实现：qt=毛玻璃新界面（默认，PySide6），tk=旧界面（回退用）",
    )
    args = parser.parse_args(argv)

    # PyInstaller 在 Windows 上需要用 freeze_support 保护多进程入口
    multiprocessing.freeze_support()
    _ensure_package_on_path()
    os.environ.setdefault("TK_SILENCE_DEPRECATION", "1")

    if args.version:
        from qgb.version import APP_NAME, __version__

        text = f"{APP_NAME} v{__version__}"
        try:
            print(text)
        except Exception:
            pass
        return 0

    if args.selftest:
        out = Path(args.out).expanduser() if args.out else None
        return _run_selftest(out)

    # 界面选择：--ui qt 走 PySide6 毛玻璃界面；默认仍是 Tk（迁移期可随时回退）
    # 默认走 Qt 新界面；PySide6 不可用时**自动回退**到 Tk 旧界面，
    # 保证任何环境下双击 exe 都能开出界面。
    use_qt = args.ui == "qt"
    if use_qt:
        try:
            import PySide6  # noqa: F401
        except ImportError:
            try:
                print("未安装 PySide6，回退到旧界面（pip install PySide6 可启用新界面）",
                      file=sys.stderr)
            except Exception:
                pass
            use_qt = False
    if use_qt:
        try:
            from qgb.qt.app import main as qt_main
        except ImportError as exc:
            message = (f"导入新界面失败：{exc}\n"
                       f"请先安装：pip install PySide6\n"
                       f"或改用旧界面：python run_bridge.py --ui tk")
            try:
                print(message, file=sys.stderr)
            except Exception:
                pass
            return 2
        return qt_main([sys.argv[0]])

    try:
        from qgb.gui.app import main as gui_main
    except ImportError as exc:
        message = f"导入界面模块失败：{exc}\n请确认依赖完整（pip install -r requirements.txt）"
        try:
            print(message, file=sys.stderr)
        except Exception:
            pass
        return 2

    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
