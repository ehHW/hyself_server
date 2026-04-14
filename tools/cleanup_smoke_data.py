from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools._bootstrap import bootstrap_django

bootstrap_django()

from manage import main


if __name__ == "__main__":
    sys.argv = [sys.argv[0], "cleanup_smoke_data", *sys.argv[1:]]
    main()