"""Development entry point:  python run.py

Kept as a one-liner at the project root so there's an obvious "start here"
file, and so the Windows startup registry entry has a stable path to point at.
"""

import sys

from water_buddy.app import main

if __name__ == "__main__":
    sys.exit(main())
