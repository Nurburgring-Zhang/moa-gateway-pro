import subprocess
import os
os.environ['PYTHONIOENCODING'] = 'utf-8'
r = subprocess.run(['git', 'add', '-A'], capture_output=True)
r2 = subprocess.run(['git', 'commit', '-m', 'v1.7.0 Round-1+2: 80 to 0 deep e2e fail, Service Layer + AgentDispatch + Workflow'], capture_output=True)
print('stdout:', r2.stdout.decode('utf-8', errors='replace'))
print('stderr:', r2.stderr.decode('utf-8', errors='replace'))
