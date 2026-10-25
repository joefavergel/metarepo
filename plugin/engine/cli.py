#!/usr/bin/env python3
"""metarepo engine entry point. A thin dispatcher: no logic lives here (003/ADR-001).

Commands are modules under metarepo_engine/commands/, listed in its registry.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from metarepo_engine.dispatch import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
