"""Add from_dict to Aggregator."""
from pathlib import Path
import re

p = Path(r"D:\MoA Gateway Pro\moa_gateway\capability\n_layer_moa.py")
src = p.read_text(encoding="utf-8")
if "def from_dict" in src:
    print("already has from_dict")
else:
    m = re.search(r"(@dataclass\nclass Aggregator[^:]*:[^\n]*\n(?:[ \t]+[^\n]*\n){1,20})", src)
    if m:
        block = m.group(0)
        addition = (
            "\n"
            "    @classmethod\n"
            "    def from_dict(cls, d: dict) -> 'Aggregator':\n"
            '        """Aggregator 接受 role/synthesis_prompt 别名"""\n'
            "        return cls(\n"
            '            name=d.get("name", ""),\n'
            '            model_id=d.get("model_id", ""),\n'
            '            synthesis_prompt=d.get("synthesis_prompt", d.get("role", "")),\n'
            "        )\n"
        )
        new = block + addition
        src = src.replace(block, new)
        p.write_text(src, encoding="utf-8")
        print("added")
