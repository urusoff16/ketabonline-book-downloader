"""Paths and worker launch command for source and Windows distributions."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def data_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "KetabOnlineDownloader"
    return ROOT


def worker_command() -> list[str]:
    executable = Path(sys.executable)
    if getattr(sys, "frozen", False):
        return [str(executable.with_name("ketab-worker.exe"))]
    # pythonw has no standard streams; use the console interpreter with its window hidden.
    if executable.name.lower() == "pythonw.exe":
        console = executable.with_name("python.exe")
        if console.exists():
            executable = console
    return [str(executable), "-u", str(ROOT / "download.py")]
