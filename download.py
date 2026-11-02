from ketab_downloader.cli import main
import sys


if __name__ == "__main__":
    if getattr(sys, "frozen", False):
        for stream in (sys.stdout, sys.stderr):
            if stream is not None:
                stream.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
