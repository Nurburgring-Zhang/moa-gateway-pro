import subprocess
r = subprocess.run(['git', 'show', 'HEAD:moa_gateway/server.py'], capture_output=True)
content = r.stdout.decode('utf-8', errors='replace')
lines = content.splitlines()
for i, line in enumerate(lines):
    if '@app.exception_handler' in line or 'app = FastAPI' in line:
        print(f'line {i+1}: {line}')
