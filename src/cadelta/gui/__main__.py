"""Package entry point for the CADelta desktop app."""
from __future__ import annotations

import sys

from cadelta.gui.app import main


if __name__ == "__main__":
    sys.exit(main())
