from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools._bootstrap import bootstrap_django

bootstrap_django()

from init_roles import init_basic_roles


if __name__ == "__main__":
    init_basic_roles()