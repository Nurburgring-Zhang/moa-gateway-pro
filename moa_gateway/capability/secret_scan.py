"""9 类硬编码密钥扫描 + 3 层豁免 (来自 moa-skill + moat-ops-auditor)

真实实现,非 mock。所有 9 个检测器基于正则,实际可用。
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ============ 9 类密钥检测正则 (来自 05 moa-skill + 07 moat) ============

SECRET_PATTERNS: list[tuple[str, str, int]] = [
    # (pattern_id, regex, severity 0-3)
    ("AWS_ACCESS_KEY", r"AKIA[0-9A-Z]{16}", 3),
    ("AWS_SECRET_KEY", r"(?i)aws.{0,20}(?:secret|private).{0,20}[A-Za-z0-9/+=]{40}", 3),
    ("GITHUB_PAT", r"ghp_[a-zA-Z0-9]{36,}", 3),
    ("GITHUB_FINE_GRAINED_PAT", r"github_pat_[a-zA-Z0-9_]{82}", 3),
    ("GITHUB_OAUTH", r"gho_[a-zA-Z0-9]{36,}", 3),
    ("GITHUB_USER_TOKEN", r"ghu_[a-zA-Z0-9]{36,}", 3),
    ("GITHUB_REFRESH_TOKEN", r"ghr_[a-zA-Z0-9]{36,}", 3),
    ("GITHUB_SERVER_TOKEN", r"ghs_[a-zA-Z0-9]{36,}", 3),
    ("GITHUB_ROUTING_KEY", r"ghr_[a-zA-Z0-9]{36,}", 3),
    ("GITLAB_PAT", r"glpat-[a-zA-Z0-9_\-]{20,}", 3),
    ("SLACK_BOT_TOKEN", r"xox[baprs]-[0-9]{10,12}-[0-9]{10,12}-[A-Za-z0-9]{24}", 3),
    ("SLACK_WEBHOOK", r"https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]+", 2),
    ("GOOGLE_API_KEY", r"AIza[0-9A-Za-z_\-]{35}", 3),
    ("OPENAI_API_KEY", r"sk-(?:proj-)?[a-zA-Z0-9_\-]{20,}", 3),
    ("ANTHROPIC_API_KEY", r"sk-ant-(?:api03-)?[a-zA-Z0-9_\-]{20,}", 3),
    ("STRIPE_LIVE_KEY", r"sk_live_[0-9a-zA-Z]{24,}", 3),
    ("STRIPE_TEST_KEY", r"sk_test_[0-9a-zA-Z]{24,}", 2),
    ("PRIVATE_KEY_BLOCK", r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----", 3),
    ("JWT_TOKEN", r"eyJ[A-Za-z0-9_=\-]+\.eyJ[A-Za-z0-9_=\-]+\.[A-Za-z0-9_\-]+", 2),
    # 国产厂商
    ("DEEPSEEK_API_KEY", r"sk-[a-f0-9]{32}", 3),
    ("ZHIPU_API_KEY", r"[a-f0-9]{32}\.[A-Za-z0-9]{40,}", 3),
    ("MOONSHOT_API_KEY", r"sk-[a-zA-Z0-9_\-]{48,}", 3),
    ("QWEN_API_KEY", r"sk-[a-f0-9]{32}", 3),
    ("DOUBAO_API_KEY", r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}", 2),
    ("SILICONFLOW_API_KEY", r"sk-[a-zA-Z0-9]{32}", 3),
]


# ============ 3 层豁免机制 (来自 07 moat) ============

# 豁免原因分类
EXEMPT_REASONS = frozenset(
    {
        "test",  # 测试 fixture
        "example",  # 文档示例
        "mock",  # mock 数据
        "placeholder",  # 占位符
        "fake",  # 假数据
        "TODO",  # 占位
        "revoked",  # 已撤销
    }
)


# ============ 真实使用 dataclass ============


@dataclass
class Finding:
    """一个密钥发现"""

    pattern_id: str
    severity: int  # 0-3
    file: str  # 相对路径
    line: int
    column: int
    match: str  # 原始匹配(可能被脱敏)
    redacted: str  # 脱敏版本
    context: str  # 前后 1-2 行
    exempt: bool = False
    exempt_reason: str | None = None
    exempt_source: str | None = None  # "inline" / "file_frontmatter" / "config"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScanResult:
    """一次扫描结果"""

    scanned_files: int = 0
    scanned_lines: int = 0
    scanned_at: float = field(default_factory=time.time)
    findings: list[Finding] = field(default_factory=list)
    exempt_count: int = 0
    real_count: int = 0
    by_pattern: dict[str, int] = field(default_factory=dict)
    by_severity: dict[int, int] = field(default_factory=lambda: {0: 0, 1: 0, 2: 0, 3: 0})
    blocked: bool = False
    block_reasons: list[str] = field(default_factory=list)

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)
        if finding.exempt:
            self.exempt_count += 1
        else:
            self.real_count += 1
        self.by_pattern[finding.pattern_id] = self.by_pattern.get(finding.pattern_id, 0) + 1
        self.by_severity[finding.severity] = self.by_severity.get(finding.severity, 0) + 1

    def to_dict(self) -> dict:
        return {
            "scanned_files": self.scanned_files,
            "scanned_lines": self.scanned_lines,
            "scanned_at": self.scanned_at,
            "findings": [f.to_dict() for f in self.findings],
            "exempt_count": self.exempt_count,
            "real_count": self.real_count,
            "by_pattern": self.by_pattern,
            "by_severity": self.by_severity,
            "blocked": self.blocked,
            "block_reasons": self.block_reasons,
        }


# ============ 脱敏 (来自 05 moa-skill) ============


def redact(match: str) -> str:
    """脱敏 secret,保留前 4 + 后 4 字符"""
    if len(match) <= 12:
        return match[:3] + "***" + match[-2:] if len(match) >= 5 else "***"
    return match[:4] + "***" + match[-4:]


# ============ 豁免检测 ============

EXEMPT_INLINE_RE = re.compile(
    r"#\s*moat:ignore\s*=\s*(\w[\w-]*)(?:\s+reason\s*=\s*\"([^\"]+)\")?",
    re.IGNORECASE,
)

EXEMPT_FILE_RE = re.compile(
    r"^---\s*\n.*?moat-disable:\s*\[\s*([^\]]+?)\s*\].*?\n---\s*\n",
    re.MULTILINE | re.DOTALL,
)

GLOBAL_EXEMPT_KEYS_PATH = ".moat/exempt.yaml"


def _load_global_exempts(root: Path) -> dict[str, list[str]]:
    """从 .moat/exempt.yaml 加载全局豁免配置"""
    path = root / GLOBAL_EXEMPT_KEYS_PATH
    if not path.exists():
        return {}
    try:
        data: dict = {}
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                k, v = line.split(":", 1)
                data[k.strip()] = [s.strip() for s in v.split(",") if s.strip()]
        return data
    except Exception:
        return {}


def is_exempt(
    file: str, line: int, line_text: str, all_lines: list[str], global_exempts: dict[str, list[str]]
) -> tuple[bool, str | None, str | None]:
    """返回 (是否豁免, 原因, 来源)"""
    # 1. inline: `# moat:ignore=PATTERN_ID reason="..."`
    m = EXEMPT_INLINE_RE.search(line_text)
    if m:
        return True, m.group(2) or "inline", "inline"
    # 2. file frontmatter
    fm = EXEMPT_FILE_RE.search("\n".join(all_lines[: min(20, len(all_lines))]))
    if fm:
        patterns = [p.strip().strip("'\"") for p in fm.group(1).split(",")]
        if "all" in patterns:
            return True, "file_frontmatter:all", "file_frontmatter"
    # 3. global config
    rel = file.replace("\\", "/")
    if rel in global_exempts:
        return True, "global:config", "config"
    # 4. 路径名含 exempt/fixture/example/mock/test/参考/分析
    path_lower = file.lower()
    for kw in (
        "exempt",
        "fixture",
        "example",
        "mock",
        "test_data",
        ".template",
        "test_",
        "/test",
        "tests/",
        "_test.",
        "参考",
        "分析",
    ):  # 参考项目 + analysis docs
        if kw in path_lower:
            return True, f"path:{kw}", "path"
    return False, None, None


# ============ 核心扫描器 ============

# 默认扫描时跳过的目录
DEFAULT_SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "__pycache__",
        "dist",
        "build",
        ".pytest_cache",
        "data",
        "data.bak",
        "extracted",
        ".mypy_cache",
        ".tox",
        ".eggs",
        "*.egg-info",
        "参考",  # 用户提供的参考项目
        "scripts",  # 测试/调试脚本可能有 mock 假 key
    }
)

# 默认扫描时跳过的文件
DEFAULT_SKIP_FILES = frozenset(
    {
        ".fernet_key",
        ".jwt_secret",
    }
)


def _is_text_file(p: Path) -> bool:
    """粗略判断是否文本文件(避免读 binary)"""
    if p.suffix.lower() in {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".zip",
        ".tar",
        ".gz",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".pdf",
        ".mp4",
        ".mp3",
        ".woff",
        ".woff2",
        ".ttf",
        ".ico",
    }:
        return False
    try:
        with p.open("rb") as f:
            chunk = f.read(8192)
        # 启发式:大量 null byte = binary
        if b"\x00" in chunk:
            return False
        # 检查可打印字符比例(>= 85% 视为文本)
        printable = sum(1 for b in chunk if 32 <= b < 127 or b in (9, 10, 13) or b >= 128)
        return not (chunk and printable / len(chunk) < 0.85)
    except Exception:
        return False


def scan_text(text: str, file: str, root: Path | None = None) -> list[Finding]:
    """扫描一段文本,返回 Finding 列表"""
    findings: list[Finding] = []
    lines = text.splitlines()
    for i, line in enumerate(lines, 1):
        for pid, pat, sev in SECRET_PATTERNS:
            for m in re.finditer(pat, line):
                match = m.group(0)
                col = m.start() + 1
                ctx_start = max(0, i - 2)
                ctx_end = min(len(lines), i + 1)
                context = "\n".join(lines[ctx_start:ctx_end])
                exempt, reason, source = False, None, None
                if root is not None:
                    global_exempts = _load_global_exempts(root)
                    exempt, reason, source = is_exempt(file, i, line, lines, global_exempts)
                findings.append(
                    Finding(
                        pattern_id=pid,
                        severity=sev,
                        file=file,
                        line=i,
                        column=col,
                        match=match,
                        redacted=redact(match),
                        context=context,
                        exempt=exempt,
                        exempt_reason=reason,
                        exempt_source=source,
                    )
                )
    return findings


def scan_path(
    root: Path,
    patterns: list[str] | None = None,
    skip_dirs: Iterable[str] | None = None,
    skip_files: Iterable[str] | None = None,
    max_file_bytes: int = 2 * 1024 * 1024,  # 2MB
) -> ScanResult:
    """扫描一个目录,返回 ScanResult"""
    root = Path(root).resolve()
    if not root.exists():
        raise FileNotFoundError(f"path not found: {root}")
    patterns = patterns or ["**/*"]
    skip_dirs = set(skip_dirs or DEFAULT_SKIP_DIRS)
    skip_files = set(skip_files or DEFAULT_SKIP_FILES)
    result = ScanResult()
    _load_global_exempts(root)
    for pat in patterns:
        for p in root.glob(pat):
            if not p.is_file():
                continue
            # 跳过大文件
            try:
                if p.stat().st_size > max_file_bytes:
                    continue
            except OSError:
                continue
            # 跳过特定文件名
            if p.name in skip_files:
                continue
            # 跳过路径里的 skip_dirs
            try:
                rel_parts = p.relative_to(root).parts
            except ValueError:
                continue
            if any(part in skip_dirs for part in rel_parts):
                continue
            # 文本检测
            if not _is_text_file(p):
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            rel = str(p.relative_to(root)).replace("\\", "/")
            result.scanned_files += 1
            result.scanned_lines += text.count("\n") + 1
            for f in scan_text(text, rel, root):
                result.add(f)
    # 决定是否 block(只算真实发现)
    if should_block(result, fail_on=3):
        result.blocked = True
        result.block_reasons.append(f"found {result.real_count} real secrets (severity >= 3)")
    return result


def should_block(result: ScanResult, fail_on: int = 3) -> bool:
    """根据 fail_on severity 决定是否 block (CI gate)
    只看非豁免的发现 — 豁免的不算 block
    """
    return any(f.severity >= fail_on for f in result.findings if not f.exempt)


# ============ CLI 入口 ============


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="MoA Gateway Pro 密钥扫描器")
    parser.add_argument("path", nargs="?", default=".", help="扫描路径(默认 .)")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument("--fail-on", type=int, default=3, help="失败 severity 阈值")
    parser.add_argument("--no-block", action="store_true", help="不 block,只 warn")
    args = parser.parse_args(argv)
    result = scan_path(Path(args.path))
    blocked = should_block(result, args.fail_on) and not args.no_block
    if args.json:
        out = result.to_dict()
        out["blocked"] = blocked
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"扫描 {result.scanned_files} 文件, {result.scanned_lines} 行")
        print(f"  真实发现: {result.real_count} (豁免: {result.exempt_count})")
        for sev in (3, 2, 1, 0):
            n = result.by_severity.get(sev, 0)
            if n:
                print(f"  severity {sev}: {n}")
        for f in result.findings:
            if f.exempt:
                continue
            tag = "🚨" if f.severity >= 3 else "⚠️"
            print(
                f"  {tag} {f.pattern_id} {f.file}:{f.line}:{f.column} → {f.redacted}"
                f" {'[' + f.exempt_reason + ']' if f.exempt_reason else ''}"
            )
    return 1 if blocked else 0


if __name__ == "__main__":
    sys_exit = __import__("sys").exit
    sys_exit(main())
