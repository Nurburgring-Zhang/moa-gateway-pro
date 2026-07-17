"""P0-9 + P0-10: Fix duplicate _bcrypt defs and logger order in storage.py."""
from pathlib import Path

p = Path(r"D:\MoA Gateway Pro\moa_gateway\storage.py")
src = p.read_text(encoding="utf-8")

# Remove the second copy of _bcrypt_hash + _bcrypt_verify (after async_* funcs)
old = '''

def _bcrypt_hash(password: str) -> str:
    """bcrypt 原生 API,绕过 passlib 兼容问题"""
    pwd = password.encode("utf-8")[:72]   # bcrypt 硬限制 72 字节
    return bcrypt.hashpw(pwd, bcrypt.gensalt(rounds=12)).decode("utf-8")


def _bcrypt_verify(password: str, hashed: str) -> bool:
    pwd = password.encode("utf-8")[:72]
    try:
        return bcrypt.checkpw(pwd, hashed.encode("utf-8"))
    except Exception:
        return False
'''
count = src.count(old)
print(f"found {count} duplicate _bcrypt blocks")
src = src.replace(old, "", count)  # remove all duplicates
# Add logger BEFORE the first _bcrypt (line 32)
old2 = '''import bcrypt
from cryptography.fernet import Fernet

from .config import DATA_DIR, Settings, get_settings


def _bcrypt_hash'''
new2 = '''import bcrypt
from cryptography.fernet import Fernet

from .config import DATA_DIR, Settings, get_settings

logger = logging.getLogger(__name__)


def _bcrypt_hash'''
if old2 in src:
    src = src.replace(old2, new2, 1)
    print("P0-10: moved logger before _bcrypt")
else:
    # already moved or different content
    print("P0-10: no change needed (already done)")

p.write_text(src, encoding="utf-8")
print("done")
