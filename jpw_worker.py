"""Entry point for the packaged worker executable (jpw-worker.exe)."""

import sys

from gui.worker import main

if __name__ == "__main__":
    sys.exit(main())
