"""打包 v1.5.0+ zip — 排除 venv / data / 临时脚本 / log / 测试输出 / secrets
快:用 store 而非 deflate (这些是源码,大多压缩不了多少)

v1.8.1+: 加严排除规则,防止临时脚本/log/调试文件混进发布包
"""
import os
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path.cwd()
OUT_DIR = ROOT / "zip"
OUT_DIR.mkdir(exist_ok=True)

# 自动检测 version
_git = os.popen("git describe --tags --abbrev=0 2>nul").read().strip() or "v1.8.1"
OUT = OUT_DIR / f"MoA Gateway Pro {_git}.zip"

EXCLUDE_DIRS = {
    ".venv", "venv", "env", "ENV",
    "__pycache__", ".pytest_cache", ".idea", ".vscode",
    ".ruff_cache", ".mypy_cache",  # linter/typechecker 缓存
    "data", "data.bak", "build", "dist", ".moa-gateway",
    "worktrees", "tmp", "temp", "logs",
    "extracted", ".moat", ".github", "node_modules",
    "ComfyUI",  # 系统级其他项目
    "参考",
    "zip",  # 排除 zip 自身,防止递归
    ".git",   # git 内部对象(可能被脚本路径意外扫到)
    ".moai",  # checkpoint
    "deploy/redis",  # redis-server.exe + .pdb + zip (1.6MB+5MB+调试符号)
    "deploy/pg",  # PostgreSQL data dir (WAL 16MB etc)
    "deploy/prometheus",  # prometheus binary if installed
    "deploy/node_exporter",  # node_exporter binary
    "deploy/.cache",  # 部署缓存
}

# 单文件名白名单(精确匹配)
EXCLUDE_FILES_EXACT = {
    ".fernet_key", ".jwt_secret", ".env", ".env.local",
    "endpoints_scan.json",
    "debug_tmp.py",
}

# 单文件名 glob 模式(所有路径都生效)
EXCLUDE_FILE_PATTERNS = [
    re.compile(r"^_\w+\.py$"),         # _*.py 临时脚本
    re.compile(r"^test_pat\d*\.py$"),   # test_pat.py / test_pat2.py ...
    re.compile(r"^test_pat\.py$"),
    re.compile(r"^debug_\w+\.py$"),     # debug_*.py 调试脚本
    re.compile(r"^out_.+\.(txt|log)$"), # out_*.txt / out_*.log
    re.compile(r"^out\.txt$"),
    re.compile(r"^out\d+\.txt$"),       # out20.txt, out1.txt 等
    re.compile(r"^.*\.log$"),           # 所有 .log
    re.compile(r"^.*\.db$"),
    re.compile(r"^.*\.zip$"),
    re.compile(r"^.*\.pem$"),
    re.compile(r"^.*\.key$"),
    re.compile(r"^.*\.pyc$"),
    re.compile(r"^.*\.pyo$"),
    re.compile(r"^.*\.pyd$"),
    re.compile(r"^.*\.so$"),
    re.compile(r"^.*\.dll$"),
    re.compile(r"^.*\.pdb$"),
    re.compile(r"^analyze_results\.py$"),
    re.compile(r"^find_bare_except\.py$"),
    re.compile(r"^start_test\.py$"),
    re.compile(r"^\.coverage$"),       # pytest 覆盖率单文件
    re.compile(r"^\.coverage\..+$"),   # .coverage.<host> 多文件
    re.compile(r"^_reqs\..+$"),         # 临时 pip list 快照
]

# 任何路径下都生效的 test_*.py (包括子目录)
ANYWHERE_FILE_PATTERNS = [
    re.compile(r"^test_.+\.py$"),
    re.compile(r"^test_.+\.json$"),
    re.compile(r"^test_.+\.txt$"),
    re.compile(r"^test_.+\.log$"),
]

# 路径里包含特定子串的也排除
EXCLUDE_PATH_SUBSTR = (
    "scripts/_",  # scripts/_*.py 临时脚本
    "out_srv",    # server log files
)

# 多段路径前缀(解决 deploy/redis 之类的子目录排除)
# parts[:N] == prefix 形式匹配,例如 ('deploy','redis') 命中所有 deploy/redis/xxx
EXCLUDE_PATH_PREFIXES = (
    ("deploy", "redis"),
    ("deploy", "pg"),
    ("deploy", "prometheus"),
    ("deploy", "node_exporter"),
    ("deploy", ".cache"),
    ("moa_gateway", "capability", "tests"),
)


def should_exclude(path: Path) -> bool:
    parts = set(path.parts)
    if parts & EXCLUDE_DIRS:
        return True
    # 多段路径前缀
    for prefix in EXCLUDE_PATH_PREFIXES:
        n = len(prefix)
        if tuple(path.parts[:n]) == prefix:
            return True
    name = path.name
    if name in EXCLUDE_FILES_EXACT:
        return True
    for pat in EXCLUDE_FILE_PATTERNS:
        if pat.match(name):
            return True
    # test_*.py 任何路径下都排除 (顶层 + 子目录)
    for pat in ANYWHERE_FILE_PATTERNS:
        if pat.match(name):
            return True
    posix = str(path).replace(os.sep, "/")
    if any(sub in posix for sub in EXCLUDE_PATH_SUBSTR):
        return True
    return False


# 先扫描看会有多少
file_list = []
total_size = 0
for p in ROOT.rglob("*"):
    if not p.is_file():
        continue
    rel = p.relative_to(ROOT)
    if should_exclude(rel):
        continue
    file_list.append(p)
    total_size += p.stat().st_size

print(f"will pack {len(file_list)} files, raw size {total_size/1024/1024:.1f} MB")
sys.stdout.flush()
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

count = 0
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=3) as zf:
    for p in file_list:
        rel = p.relative_to(ROOT)
        arcname = str(rel).replace(os.sep, "/")
        zf.write(p, arcname)
        count += 1
        if count % 100 == 0:
            print(f"  {count}/{len(file_list)}")
            sys.stdout.flush()

print(f"[OK] {OUT}")
print(f"  {count} files, raw {total_size/1024/1024:.1f} MB → zip {OUT.stat().st_size/1024/1024:.1f} MB")
print(f"  path: {OUT}")
