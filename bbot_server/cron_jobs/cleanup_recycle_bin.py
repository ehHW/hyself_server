from bbot.recycle_bin import cleanup_expired_recycle_bin


def run() -> dict[str, int]:
    """清理回收站中超过 30 天的文件与空目录。"""
    return cleanup_expired_recycle_bin(days=30)
