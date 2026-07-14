"""Revert bad patch and re-apply correctly."""
import re
from pathlib import Path

p = Path(r"D:\MoA Gateway Pro\moa_gateway\server.py")
src = p.read_text(encoding="utf-8")

# First: revert the bad inserts (the ones that landed AFTER except Exception)
# The bad pattern is:
#   "        except Exception as e:\n        except HTTPException:\n            raise  # patch v1.6.6"
# The good pattern needs to be:
#   "        except HTTPException:\n            raise  # patch v1.6.6\n        except Exception as e:"
src2 = re.sub(
    r"(\n\s+)except Exception as e:\n\s+except HTTPException:\n\s+raise  # patch v1\.6\.6: pass through 4xx",
    r"\1except HTTPException:\n\1    raise  # patch v1.6.6: pass through 4xx\n\1except Exception as e:",
    src,
)

p.write_text(src2, encoding="utf-8")
print("reverted bad inserts, fixed order")
