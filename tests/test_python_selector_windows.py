"""Pruebas reales del selector BAT: ejecutar en Windows con Python 3."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import venv


@unittest.skipUnless(os.name == 'nt', 'requiere cmd.exe y entornos de Windows')
class PythonSelectorWindowsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='bot con espacios ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        source = Path(__file__).resolve().parents[1] / 'seleccionar_python_bot.bat'
        shutil.copyfile(source, self.root / source.name)
        self.env = os.environ.copy()
        self.env.pop('CONTENTAPP_PYTHON', None)
        self.env.pop('PYTHONHOME', None)
        self.env['BOT_PYTHON'] = 'no debe conservarse este valor'

    def environment(self, name, broken=False):
        folder = self.root / name
        venv.EnvBuilder(with_pip=False).create(folder)
        python = folder / 'Scripts' / 'python.exe'
        if broken:
            missing = self.root / 'Python eliminado'
            (folder / 'pyvenv.cfg').write_text(
                f'home = {missing}\ninclude-system-site-packages = false\n'
                f'executable = {missing / "python.exe"}\n', encoding='utf-8')
            probe = subprocess.run([str(python), '-c', 'pass'], env=self.env,
                                   capture_output=True, timeout=30)
            self.assertNotEqual(probe.returncode, 0, 'El entorno debe estar roto')
        return python

    def select(self):
        # Guardar el resultado antes del echo: este no debe ocultar un fallo.
        driver = self.root / 'seleccionar.bat'
        driver.write_text('@echo off\ncall "%~dp0seleccionar_python_bot.bat"\n'
                          'set "RESULTADO=%errorlevel%"\n'
                          'echo ELEGIDO=%BOT_PYTHON%\nexit /b %RESULTADO%\n')
        result = subprocess.run([os.environ['COMSPEC'], '/d', '/c', str(driver)],
                                cwd=self.root, env=self.env, capture_output=True,
                                text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        selected = next(line[len('ELEGIDO='):] for line in result.stdout.splitlines()
                        if line.startswith('ELEGIDO='))
        return Path(selected), result.stdout

    def test_skips_venv_whose_base_python_was_removed(self):
        broken = self.environment('.venv', broken=True)
        working = self.environment('venv')
        selected, output = self.select()
        self.assertEqual(selected, working)
        self.assertIn('[AVISO]', output)
        self.assertTrue(broken.exists(), 'No debe borrar el entorno viejo')

    def test_keeps_valid_project_environment(self):
        working = self.environment('.venv')
        selected, output = self.select()
        self.assertEqual(selected, working)
        self.assertNotIn('[AVISO]', output)

    def test_invalid_override_falls_back_to_project_environment(self):
        broken = self.environment('override', broken=True)
        working = self.environment('.venv')
        self.env['CONTENTAPP_PYTHON'] = str(broken)
        selected, output = self.select()
        self.assertEqual(selected, working)
        self.assertIn('[AVISO]', output)

    def test_valid_override_has_priority(self):
        self.environment('.venv')
        self.env['CONTENTAPP_PYTHON'] = sys.executable
        selected, _ = self.select()
        self.assertEqual(selected, Path(sys.executable))


if __name__ == '__main__':
    unittest.main()
