"""Nightly evaluation CLI entry point (C-07)."""

import asyncio
import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from app.eval.nightly import _main

if __name__ == "__main__":
    asyncio.run(_main())