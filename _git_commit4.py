import subprocess
import os
os.environ['PYTHONIOENCODING'] = 'utf-8'
r = subprocess.run(['git', 'add', '-A'], capture_output=True)
r2 = subprocess.run(['git', 'commit', '-m', 'v1.7.3 Round-5: all 7 workflows pass with real inter-module data flow'], capture_output=True)
print('commit:', r2.stdout.decode('utf-8', errors='replace')[:1000])
print('stderr:', r2.stderr.decode('utf-8', errors='replace')[:500])
