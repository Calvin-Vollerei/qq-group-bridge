"""离线端到端冒烟：不需要任何真实账号，验证整条搬运链路。

运行::

    python -m qgb.dev.smoke

覆盖场景：
  1. 扫描群文件 → 规则过滤（命中 / 未命中）
  2. 下载（含断点续传路径）
  3. 上传到「假网盘」（本地目录）
  4. 去重（第二轮不应重复搬运）
  5. 临时文件清理
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from ..config import save_config
from ..logging_setup import get_logger, setup_logging
from ..models import GroupFile, TransferState
from ..napcat.client import FakeOneBotClient
from ..pipeline import Pipeline
from ..store import StateStore
from ..uploaders.local import LocalUploader
from .fakes import RangeFileServer, make_config, make_secrets

log = get_logger(__name__)


def _mkfile(path: Path, size: int, seed: bytes = b"QQGROUP\x00") -> bytes:
    """造一个内容可复现的假文件。"""
    chunk = (seed * (size // len(seed) + 1))[:size]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(chunk)
    return chunk


def run_smoke(*, workdir: Path | None = None, verbose: bool = True) -> dict:
    """执行一次完整离线验证，返回结果字典。"""
    root = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="qgb-smoke-"))
    root.mkdir(parents=True, exist_ok=True)

    setup_logging(root / "logs", console=verbose)
    src_dir = root / "group_files"

    # ---- 造「群里的」三个文件：两个该抓、一个不该抓
    pdf_name = "季度报告_v3.pdf"
    xlsx_name = "数据_2026Q1.xlsx"
    docx_name = "会议记录.docx"

    pdf_bytes = _mkfile(src_dir / pdf_name, 320 * 1024, b"PDFDATA\x00")
    xlsx_bytes = _mkfile(src_dir / xlsx_name, 96 * 1024, b"XLSXDATA\x00")
    _mkfile(src_dir / docx_name, 40 * 1024, b"DOCXDATA\x00")

    results: dict = {"root": str(root), "steps": []}

    def step(name: str, ok: bool, detail: str = "") -> None:
        results["steps"].append({"name": name, "ok": bool(ok), "detail": detail})
        if verbose:
            mark = "✅" if ok else "❌"
            print(f"  {mark} {name}" + (f" —— {detail}" if detail else ""))

    group_id = "123456789"

    with RangeFileServer(
        {pdf_name: src_dir / pdf_name, xlsx_name: src_dir / xlsx_name},
        support_range=True,
    ) as server:
        client = FakeOneBotClient(
            files_by_group={
                group_id: [
                    GroupFile(group_id=group_id, file_id="f-pdf-001", name=pdf_name,
                              size=len(pdf_bytes), busid=102),
                    GroupFile(group_id=group_id, file_id="f-xlsx-002", name=xlsx_name,
                              size=len(xlsx_bytes), busid=102),
                    GroupFile(group_id=group_id, file_id="f-docx-003", name=docx_name,
                              size=40960, busid=102),
                ]
            },
            urls={
                (group_id, "f-pdf-001"): server.url_for(pdf_name),
                (group_id, "f-xlsx-002"): server.url_for(xlsx_name),
            },
            online=True,
        )

        cfg = make_config(root, groups=[group_id],
                          include=[r".*\.(pdf|xlsx)$"])
        save_config(cfg, root / "config.json")

        secrets = make_secrets(root)
        store = StateStore(root / "state.db")
        uploader = LocalUploader(cfg.upload, secrets)

        events: list[str] = []
        pipeline = Pipeline(
            cfg, store, secrets, client, uploader,
            on_event=lambda e: events.append(f"{e.kind}:{e.message}" if e.message else e.kind),
        )

        # ---- 第一轮
        stats1 = pipeline.run_once()
        netdisk = Path(cfg.upload.local_root)

        # 本地适配器的路径是 <local_root>/<群号>/<文件>：
        # 它**不拼 remote_root**（local_root 本身就是根，再叠一层是多余嵌套）。
        pdf_out = netdisk / group_id / pdf_name
        xlsx_out = netdisk / group_id / xlsx_name
        docx_out = netdisk / group_id / docx_name

        step("PDF 已落盘到网盘目录", pdf_out.is_file(), str(pdf_out.relative_to(root)))
        step("XLSX 已落盘到网盘目录", xlsx_out.is_file(), str(xlsx_out.relative_to(root)))
        step("DOCX 被规则过滤（未上传）", not docx_out.exists())
        step(
            "上传内容与源文件一致",
            pdf_out.is_file() and pdf_out.read_bytes() == pdf_bytes,
            "sha256 等值校验",
        )
        step("统计：uploaded == 2", stats1.uploaded == 2, f"uploaded={stats1.uploaded}")
        step("统计：filtered >= 1", stats1.filtered >= 1, f"filtered={stats1.filtered}")

        tmp_dirs = [p for p in Path(cfg.temp_dir).iterdir()] if Path(cfg.temp_dir).is_dir() else []
        step("临时文件已清理", len(tmp_dirs) == 0, f"残留目录 {len(tmp_dirs)} 个")

        state_pdf = store.get_transfer((group_id, 102, "f-pdf-001"))
        step(
            "状态机走到 DONE",
            bool(state_pdf) and state_pdf["state"] == TransferState.DONE.value,
            state_pdf["state"] if state_pdf else "无记录",
        )
        step("sha256 已记录", bool(state_pdf and state_pdf["sha256"]), "")

        # ---- 第二轮：去重
        stats2 = pipeline.run_once()
        step("去重：第二轮无新增搬运", stats2.uploaded == 0, f"uploaded={stats2.uploaded}")
        step("去重：发现数未增加", stats2.discovered == 0, f"discovered={stats2.discovered}")

        # ---- 断点续传
        resume_ok, resume_detail = _test_resume(root, client, server, pdf_name, pdf_bytes, source=src_dir)
        step("断点续传可用", resume_ok, resume_detail)

        results["stats"] = [stats1.as_dict(), stats2.as_dict()]
        results["events_sample"] = events[-12:]
        store.close()

    results["ok"] = all(s["ok"] for s in results["steps"])
    return results


def _test_resume(root: Path, client, server, name: str, content: bytes, *, source: Path) -> tuple[bool, str]:
    """预置半截 .part 文件，验证下载器会走 Range 续传并最终一致。"""
    from ..downloader import Downloader

    work = root / "resume_test"
    work.mkdir(parents=True, exist_ok=True)
    dest = work / name
    part = dest.with_name(dest.name + ".part")

    half = len(content) // 2
    part.write_bytes(content[:half])

    dl = Downloader(max_retries=1, backoff_base=0)
    try:
        result = dl.download(server.url_for(name), dest, expected_size=len(content))
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"

    if not dest.is_file():
        return False, "目标文件未生成"
    if dest.read_bytes() != content:
        return False, "续传后内容不一致"
    return True, f"从 {half} 字节处续传，resumed_from={result.resumed_from}"


def main() -> int:
    print("=" * 66)
    print("QQ群文件搬运工 —— 离线端到端冒烟（无需真实账号）")
    print("=" * 66)

    keep = "--keep" in sys.argv
    workdir = None
    if keep:
        workdir = Path(tempfile.mkdtemp(prefix="qgb-smoke-keep-"))
        print(f"保留工作目录：{workdir}\n")

    result = run_smoke(workdir=workdir, verbose=True)

    total = len(result["steps"])
    passed = sum(1 for s in result["steps"] if s["ok"])
    print("\n" + "-" * 66)
    print(f"结果：{passed}/{total} 项通过")
    for s in result["stats"]:
        print(
            f"  统计：发现 {s['discovered']} · 过滤 {s['filtered']} · "
            f"上传 {s['uploaded']} · 失败 {s['failed']} · 流量 {s['bytes_text']}"
        )

    if not keep and not workdir:
        shutil.rmtree(result["root"], ignore_errors=True)

    print("=" * 66)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
