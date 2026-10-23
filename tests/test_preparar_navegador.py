import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import preparar_navegador_bot as setup


class BrowserSetupTests(unittest.TestCase):
    def test_reuses_installed_brave_without_downloading_chromium(self):
        with patch.object(setup.importlib.util, 'find_spec', return_value=object()), \
             patch.object(setup.navegador, 'buscar_navegador', return_value=('brave', '/brave')), \
             patch.object(setup.subprocess, 'run') as run:
            self.assertEqual(setup.preparar('auto'), 'brave')
        run.assert_not_called()

    def test_installs_browser_dependencies_with_same_python(self):
        with patch.object(setup.importlib.util, 'find_spec', return_value=None), \
             patch.object(setup.navegador, 'buscar_navegador', return_value=('brave', '/brave')), \
             patch.object(setup.subprocess, 'run') as run:
            setup.preparar('auto')
        run.assert_called_once_with([sys.executable, '-m', 'pip', 'install', 'playwright', 'psutil'], check=True)

    def test_installs_only_psutil_when_playwright_is_ready(self):
        with patch.object(setup.importlib.util, 'find_spec', side_effect=lambda name: None if name == 'psutil' else object()), \
             patch.object(setup.navegador, 'buscar_navegador', return_value=('chrome', '/chrome')), \
             patch.object(setup.subprocess, 'run') as run:
            setup.preparar('chrome')
        run.assert_called_once_with([sys.executable, '-m', 'pip', 'install', 'psutil'], check=True)

    def test_failed_install_does_not_claim_success(self):
        with patch.object(setup.importlib.util, 'find_spec', return_value=None), \
             patch.object(setup.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'pip')):
            with self.assertRaises(subprocess.CalledProcessError):
                setup.preparar('auto')


if __name__ == '__main__':
    unittest.main()
