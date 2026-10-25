"""Test package for network_config-backup.

Run with either runner:

    python3 -m unittest discover -s tests -v
    python3 -m pytest tests -v
"""

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _fixture(name: str) -> str:
    """Return the contents of a file under tests/fixtures/."""
    return (_REPO / "tests" / "fixtures" / name).read_text(encoding="utf-8")