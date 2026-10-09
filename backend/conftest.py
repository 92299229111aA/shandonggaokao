"""Root conftest: make the `app` package importable regardless of CWD.

There is intentionally no test here — this file exists so that `pytest` works
from the repo root, from backend/, and inside the container.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
