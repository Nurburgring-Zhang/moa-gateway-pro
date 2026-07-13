"""打包 v1.5.0 zip — 排除 venv / data / 参考/extracted / __pycache__ / secrets
快:用 store 而非 deflate (这些是源码,大多压缩不了多少)
"""
import os
import sys
import zipfile
from pathlib import Path

ROOT = Path.cwd()
OUT_DIR = ROOT / "zip"
OUT_DIR.mkdir(exist_ok=True)
OUT = OUT_DIR / "MoA Gateway Pro v1.6.0.zip"

EXCLUDE_DIRS = {
    ".venv", "venv", "env", "ENV",
    "__pycache__", ".pytest_cache", ".idea", ".vscode",
    "data", "data.bak", "build", "dist", ".moa-gateway",
    "worktrees", "tmp", "temp", "logs",
    "extracted", ".moat", ".github", "node_modules",
    "ComfyUI",  # 系统级其他项目
    "参考",
}
EXCLUDE_FILES = {
    ".fernet_key", ".jwt_secret", "*.db", "*.log", "*.zip",
    "*.pem", "*.key", ".env", ".env.local",
    "debug_tmp.py",
}
EXCLUDE_EXT = {".pyc", ".pyo", ".pyd", ".so", ".dll", ".pdb"}


def should_exclude(path: Path) -> bool:
    parts = set(path.parts)
    if parts & EXCLUDE_DIRS:
        return True
    if path.name in EXCLUDE_FILES:
        return True
    if path.suffix.lower() in EXCLUDE_EXT:
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

print(f"✓ {OUT}")
print(f"  {count} files, raw {total_size/1024/1024:.1f} MB → zip {OUT.stat().st_size/1024/1024:.1f} MB")
print(f"  path: {OUT}")