"""Preserve notices from the Python distribution and build bootloader."""
import importlib.metadata
import shutil
import sys
from pathlib import Path

destination = Path('dist/KetabOnline/licenses')
destination.mkdir(parents=True, exist_ok=True)
runtime = Path(sys.base_prefix)
shutil.copy2(runtime / 'LICENSE.txt', destination / 'Python-LICENSE.txt')
shutil.copy2(runtime / 'tcl/tk8.6/license.terms', destination / 'Tk-license.terms')
distribution = importlib.metadata.distribution('pyinstaller')
license_file = next(item for item in distribution.files if str(item).endswith('licenses/COPYING.txt'))
shutil.copy2(distribution.locate_file(license_file), destination / 'PyInstaller-COPYING.txt')
