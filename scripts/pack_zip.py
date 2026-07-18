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
    "data", "data.bak", "build", "dist", ".moa-gateway",
    "worktrees", "tmp", "temp", "logs",
    "extracted", ".moat", ".github", "node_modules",
    "ComfyUI",  # 系统级其他项目
    "参考",
    "zip",  # 排除 zip 自身,防止递归
}

# 单文件名白名单(精确匹配)
EXCLUDE_FILES_EXACT = {
    ".fernet_key", ".jwt_secret", ".env", ".env.local",
    "endpoints_scan.json",
    "debug_tmp.py",
}

# 单文件名 glob 模式
EXCLUDE_FILE_PATTERNS = [
    re.compile(r"^_\w+\.py$"),         # _*.py 临时脚本 (_gen_descriptions.py, _check_desc.py ...)
    re.compile(r"^test_pat\d*\.py$"),   # test_pat.py / test_pat2.py ... PAT 测试脚本
    re.compile(r"^test_pat\.py$"),
    re.compile(r"^debug_\w+\.py$"),     # debug_*.py 调试脚本
    re.compile(r"^out_.+\.(txt|log)$"), # out_*.txt / out_*.log 输出文件
    re.compile(r"^.*\.log$"),           # 所有 .log 文件
    re.compile(r"^.*\.db$"),            # .db 数据库文件
    re.compile(r"^.*\.zip$"),           # 防止 zip 递归
    re.compile(r"^.*\.pem$"),
    re.compile(r"^.*\.key$"),
    re.compile(r"^.*\.pyc$"),
    re.compile(r"^.*\.pyo$"),
    re.compile(r"^.*\.pyd$"),
    re.compile(r"^.*\.so$"),
    re.compile(r"^.*\.dll$"),
    re.compile(r"^.*\.pdb$"),
]

# 顶层(非子目录)文件 glob — 这些只在根目录是 dev 脚本
TOPLEVEL_FILE_PATTERNS = [
    re.compile(r"^test_.+\.py$"),       # 顶层 test_*.py (dev 集成测试)
    re.compile(r"^test_.+\.json$"),     # 顶层 test_*.json
    re.compile(r"^test_.+\.txt$"),      # 顶层 test_*.txt
    re.compile(r"^test_.+\.log$"),      # 顶层 test_*.log
]

# 路径里包含特定子串的也排除
EXCLUDE_PATH_SUBSTR = (
    "scripts/_",  # scripts/_*.py 临时脚本
    "out_srv",    # server log files
)


def should_exclude(path: Path) -> bool:
    parts = set(path.parts)
    if parts & EXCLUDE_DIRS:
        return True
    name = path.name
    if name in EXCLUDE_FILES_EXACT:
        return True
    for pat in EXCLUDE_FILE_PATTERNS:
        if pat.match(name):
            return True
    # 顶层文件(无父目录的 dev 脚本)
    is_toplevel = len(path.parts) == 1
    if is_toplevel:
        for pat in TOPLEVEL_FILE_PATTERNS:
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
