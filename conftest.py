"""Make the package root importable so `import wayfinder_router` works without install."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
