from __future__ import annotations

import sys
from pathlib import Path


plugin_dir = Path(__file__).resolve().parent
repo_root = plugin_dir.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from stationgen.ipc import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main([]))
