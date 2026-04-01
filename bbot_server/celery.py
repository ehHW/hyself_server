import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bbot_server.settings")

app = Celery("bbot_server")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# Windows 下 billiard/prefork 容易触发 WinError 5（信号量/权限）,
# 默认切换为 solo 池，保证开发环境可稳定执行任务。
if os.name == "nt":
	app.conf.worker_pool = "solo"
	app.conf.worker_concurrency = 1
