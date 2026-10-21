"""Move the library between Macs through iCloud Drive, one machine at a time (see paper2brain/handoff.py).

  .venv/bin/python ops/handoff.py push   # on the Mac you are leaving: stop it and snapshot everything into iCloud
  .venv/bin/python ops/handoff.py pull   # on the Mac you switch to, once iCloud has synced
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paper2brain import handoff  # noqa: E402

if __name__ == "__main__":
    {"push": handoff.push, "pull": handoff.pull}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: sys.exit(__doc__))()
