"""Allows  python -m water_buddy  as an alternative to  python run.py"""

import sys

from .app import main

if __name__ == "__main__":
    sys.exit(main())
