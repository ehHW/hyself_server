from hyself.recycle_bin import cleanup_expired_recycle_bin


def run() -> dict[str, int]:
    return cleanup_expired_recycle_bin(days=30)