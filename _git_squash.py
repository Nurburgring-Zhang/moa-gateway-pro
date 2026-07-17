import subprocess
import os
os.environ['PYTHONIOENCODING'] = 'utf-8'
# Reset to last good commit
r = subprocess.run(['git', 'reset', '--soft', '9c8adb5'], capture_output=True)
print('reset:', r.stdout.decode(), r.stderr.decode())
# Amend
r2 = subprocess.run(['git', 'commit', '-m', 'v1.7.0 Round-1+2: deep e2e 80 to 0 fail + Service Layer + AgentDispatch + Workflow engine'], capture_output=True)
print('commit:', r2.stdout.decode('utf-8', errors='replace'), r2.stderr.decode('utf-8', errors='replace'))
