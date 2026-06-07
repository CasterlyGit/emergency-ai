"""Pytest config — ensure src/ is on the path for editable-install-less runs."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Tests run against mock provider — no live API key, no DB.  Auth middleware
# must be bypassed so fixtures that call create_app(use_mock=True) don't fail
# with 401 on every /emergency request.
os.environ.setdefault("EMERGENCY_AI_NO_AUTH", "1")
os.environ.setdefault("EMERGENCY_AI_MOCK", "1")
