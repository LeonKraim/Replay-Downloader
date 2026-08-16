"""ROFL2 parsing/validation, re-exported from the proven scripts module."""
from __future__ import annotations

import os
import sys

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from rofl_parser import (  # noqa: E402,F401
    parse_file,
    parse_replay,
)

__all__ = ["parse_file", "parse_replay"]
