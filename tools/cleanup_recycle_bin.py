from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools._bootstrap import bootstrap_django

bootstrap_django()

import django

django.setup()

from hyself.recycle_bin import cleanup_expired_recycle_bin


if __name__ == "__main__":
    print(cleanup_expired_recycle_bin(days=30))