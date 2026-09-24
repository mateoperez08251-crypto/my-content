import os
import subprocess

env = os.environ.copy()
env['PATH'] = r'd:\my content' + os.pathsep + env.get('PATH', '')
script = 'import shutil, sys; print(sys.executable, shutil.which("ffmpeg"))'

subprocess.run(['python', '-c', script], cwd=r'd:\my content\Clonar-voz', env=env)
