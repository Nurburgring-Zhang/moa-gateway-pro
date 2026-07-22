import ast
from pathlib import Path

def find_long_functions(file_path, min_lines=50):
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        tree = ast.parse(content)
        long_funcs = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start = node.lineno
                if hasattr(node, 'end_lineno') and node.end_lineno:
                    end = node.end_lineno
                    length = end - start + 1
                    if length >= min_lines:
                        long_funcs.append((node.name, start, length))
        return long_funcs
    except Exception:
        return []

base_path = Path('moa_gateway')
all_long = []

for py_file in base_path.rglob('*.py'):
    long_funcs = find_long_functions(str(py_file), min_lines=100)
    for func_name, start_line, length in long_funcs:
        all_long.append((str(py_file), func_name, start_line, length))

all_long.sort(key=lambda x: -x[3])
print(f"超过100行的函数: {len(all_long)}")
for fpath, fname, start, length in all_long[:20]:
    print(f"  {fpath}:{fname} (L{start}, {length} lines)")
