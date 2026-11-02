import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import gui_runtime


class RuntimeTests(unittest.TestCase):
    def test_frozen_worker_is_an_executable_not_a_python_script(self):
        with patch.object(gui_runtime.sys, 'frozen', True, create=True), patch.object(gui_runtime.sys, 'executable', 'C:/Apps/KetabOnline.exe'):
            self.assertEqual(gui_runtime.worker_command(), ['C:\\Apps\\ketab-worker.exe'])

    def test_frozen_data_is_outside_install_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(gui_runtime.sys, 'frozen', True, create=True), patch.dict(gui_runtime.os.environ, {'LOCALAPPDATA': folder}):
                self.assertEqual(gui_runtime.data_directory(), Path(folder) / 'KetabOnlineDownloader')
