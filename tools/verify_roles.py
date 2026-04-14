from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools._bootstrap import bootstrap_django

bootstrap_django()

import django

django.setup()

from user.models import Role


if __name__ == "__main__":
    for role in Role.objects.order_by("id"):
        print(role.id, role.name, role.permissions.count())