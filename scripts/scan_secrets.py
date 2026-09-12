#!/usr/bin/env python3
"""发布前敏感信息扫描（**发布闸门**）。

用法::

    # 默认：只扫将要分发的产物（最重要）
    python scripts/scan_secrets.py

    # 连源码一起扫（跳过 tests/ 与 qgb/dev/，那里的假凭据是故意的）
    python scripts/scan_secrets.py --source

    # 指定要扫的目录/压缩包
    python scripts/scan_secrets.py dist/ 发布包.zip

命中即返回非零退出码，CI / 手动打包流程据此中止发布。

设计取舍：**默认只扫发布产物**。源码里必然有 ``refresh_token`` 之类的
字面量（正则、字段名、测试夹具），全量扫源码会产生大量噪音，反而让人
习惯性忽略告警。真正必须守住的是「别把凭据打进分发包」。

规则精度做了两层降噪，否则告警会被无视：
  * 名字里含 PASSWORD/TOKEN 的**常量定义**（如 ``KEY_..._PASSWORD = "netdisk.webdav_password"``）
    只有在右侧**看起来像密钥**时才算命中；纯小写点分/下划线标识符视为配置键名。
  * 标识符内部不匹配（``MY_PASSWORD`` 里的 ``PASSWORD`` 不再被单独抓出）。
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

# ------------------------------------------------------------------ 规则

@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]
    hint: str
    #: 需要做「是否像密码」二次判断的捕获组序号；None 表示不做
    value_group: int | None = None
    #: 是否也用于二进制扫描（exe/dll 内部）。
    #: 只有**误报率极低**的规则才标 True —— 否则冻结的 exe 里塞满了
    #: Python 标准库字符串，会立刻淹没真正的告警。
    binary: bool = False
    #: 是否适用于「第三方厂商文件」（dist-info / sboms / licenses / site-packages）。
    #: PII 类规则必须标 False：第三方库的元数据里本来就满是维护者邮箱，
    #: 全量扫描只会产出几十条噪音，让人习惯性忽略真正的告警。
    vendor_safe: bool = True


#: 前导边界：避免在标识符内部命中（KEY_X_PASSWORD 里的 PASSWORD）
_B = r"(?<![A-Za-z0-9_])"

RULES: tuple[Rule, ...] = (
    Rule(
        "疑似真实令牌赋值",
        re.compile(
            rf"""(?ix)
            {_B}
            ["']?
            (?:refresh_token|access_token|id_token|client_secret|app_secret|
               api_key|apikey|bduss|stoken|ptoken|skey|webui_?token|onebot_?token|
               password|passwd|pwd)
            ["']?
            \s*[:=]\s*
            ["']([A-Za-z0-9\-._~+/]{{10,}})["']
            """
        ),
        "凭据不得出现在分发包里；请改用 DPAPI 加密的 secrets.enc",
        value_group=1,
    ),
    Rule(
        "全大写常量疑似密钥",
        re.compile(
            rf"""(?x)
            \b[A-Z][A-Z0-9_]*(?:PASSWORD|PASSWD|TOKEN|SECRET|APIKEY|API_KEY)\b
            \s*[:=]\s*
            ["']([^"'\s]{{10,}})["']
            """
        ),
        "常量里硬编码了密钥；请改为运行时从加密凭据库读取",
        value_group=1,
    ),
    Rule(
        "百度网盘凭据",
        re.compile(r"\bBDUSS=[A-Za-z0-9\-_]{20,}|\bSTOKEN=[0-9a-f]{20,}", re.I),
        "百度 Cookie 泄露即为账号接管风险",
        binary=True,
    ),
    Rule(
        "私钥 / 证书私钥",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
        "私钥绝不能进分发包",
        binary=True,
    ),
    Rule(
        "云厂商密钥",
        re.compile(r"\b(?:AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,})\b"),
        "云平台访问密钥",
        binary=True,
    ),
    Rule(
        "内嵌 Authorization 头",
        re.compile(
            r"(?i)authorization\s*[:=]\s*[\"']?(?:bearer|basic)\s+[A-Za-z0-9\-._~+/=]{16,}"
        ),
        "硬编码的访问令牌",
        binary=True,
    ),
    Rule(
        "疑似手机号",
        re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
        "个人信息（PII）不应出现在分发包里",
        vendor_safe=False,
    ),
    Rule(
        "疑似真实邮箱",
        re.compile(r"[A-Za-z0-9._%+\-]{2,64}@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
        "确认是否为真实账号；示例请用 example.com",
        vendor_safe=False,
    ),
    Rule(
        "疑似真实群号 / QQ 号",
        re.compile(r"(?<![\d.])(?:group|群|qq|uin)\w*\s*[:=]\s*[\"']?(\d{6,12})"),
        "分发包不应内置真实群号 / QQ 号",
        value_group=1,
    ),
)

#: 适用于「第三方厂商文件」的规则子集。
#: 排除 PII 规则 —— 第三方库元数据里的维护者邮箱/测试号码不是我们的泄露。
VENDOR_RULES: tuple[Rule, ...] = tuple(r for r in RULES if r.vendor_safe)

#: 允许出现的示例值（避免误报）
#:
#: ⚠️ 这里**只放明确无害的字符串**。曾经放过 ``12345678`` 想白名单化占位群号，
#: 结果把「1 3 8 开头的那种 11 位号码」一起放过了 —— 允许清单必须是窄的。
#: 占位群号无需白名单：群号规则要求 ``group/群/qq/uin`` 关键字在前，裸数字不会命中。
ALLOWLIST = (
    "example.com",
    "example.org",
    "your-token-here",
    "changeme",
    "placeholder",
    "xxxxxxxx",
    "test-token",
    "fake",
    "dummy",
)

#: 一眼就是「配置键名」而非密钥的值：纯小写 + 点/下划线，且不含数字
_IDENTIFIER_RE = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*\Z")

#: 扫描时跳过的路径片段
SKIP_PARTS = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
}

#: 源码模式下跳过（内含故意构造的假凭据，以及**构建产物**）。
#:
#: 为什么要跳过构建产物：``dist\<发布目录>\data\`` 是运行期数据，
#: 里面装着 NapCat 的第三方文件（腾讯的邮箱、打包 JS 里的示例私钥），
#: 扫它只会产生误报、把真信号淹没。构建产物由
#: ``make_release_zip.py`` 单独按「可分发包」扫描 —— 那里的对象才是真正
#: 要发出去的东西。
SOURCE_SKIP_DIRS = {"tests", "dev", "dist", "build", "release", "发布包"}

#: 只扫这些文本后缀（二进制跳过）
TEXT_SUFFIXES = {
    ".py", ".pyi", ".txt", ".md", ".json", ".jsonl", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".conf", ".bat", ".cmd", ".ps1", ".sh", ".js", ".ts",
    ".html", ".css", ".xml", ".env", ".spec", ".log", ".csv", ".sql",
}

#: 需要做二进制扫描的后缀。
#: 关键：PyInstaller 冻结后的程序把字节码打包进 exe，**字符串字面量仍是明文**，
#: 只扫文本文件会漏掉「凭据被打进 exe」这种最严重的情况。
BINARY_SUFFIXES = {".exe", ".dll", ".pyd", ".so", ".bin", ".pkg", ".zip"}

MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_BINARY_BYTES = 256 * 1024 * 1024


@dataclass
class Finding:
    where: str
    rule: str
    excerpt: str
    hint: str


def looks_like_identifier(value: str) -> bool:
    """判断一个值是否「像配置键名而不是密钥」。

    ``netdisk.webdav_password`` → True（键名）
    ``hunter2xyz`` / ``AbC123...`` → False（可能是密钥）
    """
    if not value or any(ch.isdigit() for ch in value):
        return False
    return bool(_IDENTIFIER_RE.match(value))


def _allowed(excerpt: str) -> bool:
    low = excerpt.lower()
    return any(token in low for token in ALLOWLIST)


#: 第三方厂商文件的路径特征（这些文件里的邮箱/电话不是我们的泄露）
VENDOR_MARKERS = (
    ".dist-info", "dist-info", ".egg-info", "egg-info",
    "site-packages", "sboms", "licenses", "license",
    "third_party", "third-party", "vendor", "node_modules",
)


def is_vendor_path(path: Path) -> bool:
    """判断是否属于第三方库自带的元数据/许可证文件。"""
    parts = [p.lower() for p in path.parts]
    return any(marker in part for part in parts for marker in VENDOR_MARKERS)


#: 第三方**运行时**文件（比许可证文件更宽：整个子目录都是别人写的）
#:
#: 目前只有一种：NapCat 组件目录 `data/napcat/shell/**`。里面是 NapCat 自己的
#: 加载器与**官方 WebUI 前端**，实测会稳定误报三条：
#:   - qqnt.json / static/assets/*.js 里的厂商邮箱（QQ-Team@tencent.com 等）
#:   - Vue 生产构建残留的 `-----BEGIN PRIVATE KEY-----` 字符串常量
#:     （只出现在 npm 包 `vue/compiler-sfc` 里，它是**构建期常量**，不是私钥）
#: 这些内容不是我们写的、也无法修改（改了 NapCat 就跑不起来），
#: 因此只对这一路径放宽规则，其余规则照旧生效。
VENDOR_RUNTIME_PREFIXES = (
    "data/napcat/shell/",
)


def is_vendor_runtime(name: str) -> bool:
    """压缩包条目是否属于第三方运行时（按 zip 内路径判断）。"""
    normalized = name.replace("\\", "/").lower()
    return any(prefix in normalized for prefix in VENDOR_RUNTIME_PREFIXES)


def is_vendor_bundle(name: str) -> bool:
    """第三方**打包产物**（压缩后的 JS）。

    这是比 ``is_vendor_runtime`` 更窄的例外，也是唯一一处允许
    ``-----BEGIN PRIVATE KEY-----`` 通过的路径，理由要说清楚：

    npm 包 ``vue/compiler-sfc`` 里把这个字符串当作**字符串常量**'
    （用于识别 .pem 内容），任何用 Vue 做前端的项目编译后都会带上它。
    它出现在压缩过的 .js 里就意味着「一个字符串」而不是「一份私钥」。

    为什么必须单独放宽而不能整条禁用：真正的私钥一般是 PEM 文件
    （``.pem/.key/.crt/.p12``，或源码里的多行字面量），那些路径不在此例外内，
    私钥规则对它们**照常生效**。
    """
    normalized = name.replace("\\", "/").lower()
    return is_vendor_runtime(normalized) and "/static/" in normalized and normalized.endswith(".js")


def scan_text(text: str, where: str, rules: tuple[Rule, ...] | None = None) -> list[Finding]:
    findings: list[Finding] = []
    for rule in (rules if rules is not None else RULES):
        for match in rule.pattern.finditer(text):
            excerpt = match.group(0)
            if _allowed(excerpt):
                continue
            if rule.value_group is not None:
                try:
                    value = match.group(rule.value_group) or ""
                except IndexError:
                    value = ""
                if looks_like_identifier(value):
                    continue  # 是键名/占位符，不是密钥
                if value and _allowed(value):
                    continue
            findings.append(
                Finding(
                    where=where,
                    rule=rule.name,
                    excerpt=excerpt[:120].replace("\n", " "),
                    hint=rule.hint,
                )
            )
    return findings


def iter_files(targets: list[Path], *, skip_source_dirs: bool, include_binary: bool = False) -> list[Path]:
    out: list[Path] = []
    for target in targets:
        if target.is_file():
            out.append(target)
            continue
        for path in target.rglob("*"):
            if not path.is_file():
                continue
            if SKIP_PARTS & set(path.parts):
                continue
            if skip_source_dirs and SOURCE_SKIP_DIRS & {p.lower() for p in path.parts}:
                continue

            suffix = path.suffix.lower()
            is_text = suffix in TEXT_SUFFIXES
            is_binary = include_binary and suffix in BINARY_SUFFIXES
            if not (is_text or is_binary):
                continue

            limit = MAX_FILE_BYTES if is_text else MAX_BINARY_BYTES
            try:
                if path.stat().st_size > limit:
                    continue
            except OSError:
                continue
            out.append(path)
    return out


def scan_binary(path: Path) -> list[Finding]:
    """扫描二进制文件（冻结的 exe / dll）。

    只使用标记了 ``binary=True`` 的高信噪比规则，避免被标准库字符串淹没。
    """
    rules = [r for r in RULES if r.binary]
    if not rules:
        return []

    try:
        data = path.read_bytes()
    except OSError:
        return []

    # latin-1 是逐字节映射，不会因非法 UTF-8 丢数据
    text = data.decode("latin-1", "ignore")

    findings: list[Finding] = []
    for rule in rules:
        for match in rule.pattern.finditer(text):
            excerpt = match.group(0)
            if _allowed(excerpt):
                continue
            findings.append(
                Finding(
                    where=str(path),
                    rule=rule.name,
                    excerpt=excerpt[:120].replace("\n", " "),
                    hint=rule.hint,
                )
            )
            if len(findings) >= 50:
                return findings
    return findings


def scan_zip(path: Path) -> list[Finding]:
    findings: list[Finding] = []
    try:
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                if info.is_dir() or info.file_size > MAX_FILE_BYTES:
                    continue
                if Path(info.filename).suffix.lower() not in TEXT_SUFFIXES:
                    continue
                try:
                    data = zf.read(info)
                except (OSError, RuntimeError, zipfile.BadZipFile):
                    continue
                vendor = is_vendor_path(Path(info.filename)) or is_vendor_runtime(info.filename)
                rules = VENDOR_RULES if vendor else RULES
                if is_vendor_bundle(info.filename):
                    # 第三方压缩 JS 里允许出现 BEGIN PRIVATE KEY 字符串常量
                    rules = tuple(r for r in rules if "私钥" not in r.name)
                findings.extend(
                    scan_text(data.decode("utf-8", "ignore"),
                              f"{path.name}!{info.filename}", rules)
                )
    except (OSError, zipfile.BadZipFile) as exc:
        findings.append(Finding(str(path), "无法读取压缩包", str(exc), "确认文件是否完整"))
    return findings


def default_targets() -> tuple[list[Path], bool]:
    """返回 ``(目标列表, 是否处于「源码回退」模式)``。

    有发布产物就只扫产物；没有则回退扫源码，并**同样跳过测试夹具目录**，
    否则一次运行会被自己的测试数据淹没，闸门就形同虚设。
    """
    candidates = [Path("dist"), Path("build"), Path("release"), Path("发布包")]
    existing = [p for p in candidates if p.exists()]
    zips = list(Path(".").glob("*.zip"))
    artifacts = existing + zips

    if artifacts:
        return artifacts, False
    return [Path(".")], True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="发布前敏感信息扫描")
    parser.add_argument("targets", nargs="*", help="要扫描的目录或压缩包")
    parser.add_argument("--source", action="store_true", help="也扫源码（跳过测试夹具）")
    parser.add_argument("--quiet", action="store_true",
                        help="精简输出，仅打印结论（供 CI / 打包脚本调用）")
    args = parser.parse_args(argv)

    if args.targets:
        targets = [Path(t) for t in args.targets if Path(t).exists()]
        fallback = False
    else:
        targets, fallback = default_targets()

    skip_source_dirs = args.source or fallback
    # 扫发布产物时一并扫二进制（exe 内部的字符串字面量是明文）
    include_binary = not args.source
    files = iter_files(targets, skip_source_dirs=skip_source_dirs,
                       include_binary=include_binary)

    if not args.quiet:
        print("=" * 62)
        print("发布前敏感信息扫描")
        print("=" * 62)
        print(f"目标：{', '.join(str(t) for t in targets)}")
        print(f"扫描文件数：{len(files)}（跳过测试夹具={skip_source_dirs}，"
              f"含二进制={include_binary}）")
        if fallback:
            print("提示：未找到 dist/ 等发布产物，已回退为源码扫描。")
            print("      正式发布前请在打包后重跑本脚本，以覆盖真正的分发包。")
        print("-" * 62)

    findings: list[Finding] = []
    vendor_count = 0
    for path in files:
        suffix = path.suffix.lower()
        if suffix == ".zip":
            findings.extend(scan_zip(path))
            continue

        # 第三方库自带的元数据/许可证：只跑高信噪比规则
        vendor = is_vendor_path(path)
        if vendor:
            vendor_count += 1
        rules = VENDOR_RULES if vendor else RULES

        if suffix in BINARY_SUFFIXES:
            findings.extend(scan_binary(path))
            continue

        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        findings.extend(scan_text(text, str(path), rules))

    if not args.quiet and vendor_count:
        print(f"其中第三方库元数据文件 {vendor_count} 个（已放宽 PII 规则，仅查高信噪比项）")
        print("-" * 62)

    if not findings:
        print("✅ 未发现敏感信息，可以发布")
        return 0

    if args.quiet:
        print(f"❌ 发现 {len(findings)} 处可疑内容，已阻止发布：")
        for f in findings[:10]:
            print(f"   [{f.rule}] {f.where} :: {f.excerpt}")
        if len(findings) > 10:
            print(f"   ... 另有 {len(findings) - 10} 处")
        return 1

    print(f"❌ 发现 {len(findings)} 处可疑内容，**已阻止发布**：\n")
    for i, f in enumerate(findings[:80], 1):
        print(f"  {i:>3}. [{f.rule}] {f.where}")
        print(f"       内容：{f.excerpt}")
        print(f"       建议：{f.hint}")
    if len(findings) > 80:
        print(f"\n  ... 另有 {len(findings) - 80} 处未显示")
    print("\n处理方式：确认为误报请加入 ALLOWLIST 或改用示例值；")
    print("确认为真实凭据请立即吊销并轮换，且从产物中移除。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
