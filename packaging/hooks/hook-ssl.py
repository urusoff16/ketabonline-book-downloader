"""Preserve the optional CA file used by the bundled build interpreter."""
from pathlib import Path
import ssl

certificate_file = Path(ssl.__file__).with_name('codex-rejected-cas.pem')
datas = [(str(certificate_file), '.')] if certificate_file.is_file() else []
